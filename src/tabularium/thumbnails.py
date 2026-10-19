"""Small preview images of documents (first PDF page, or the photo itself) for the list.

Thumbnails are cached in the app home by content hash. Archived files never change, so a
cached thumbnail never goes stale; the cache can be deleted at any time and is rebuilt on use.
"""

import io
import logging
import os
import tempfile
from pathlib import Path

from tabularium import paths
from tabularium.models import Document
from tabularium.storage import Storage
from tabularium.textextract import IMAGE_TYPES, PDFIUM_LOCK

log = logging.getLogger(__name__)

WIDTH = 320  # pixels; cards show them at about half this size (sharp on high-DPI screens)
MAX_HEIGHT = 480
VERSION = 1  # bump to regenerate every thumbnail after changing how they are made


def supported(document: Document) -> bool:
    return document.mime_type == "application/pdf" or document.mime_type in IMAGE_TYPES


def thumbnail_path(storage: Storage, document: Document, cache: Path | None = None) -> Path | None:
    """Path of the document's thumbnail (made on first use), or None if it has none."""
    if not supported(document):
        return None
    cache = cache or paths.cache_dir() / "thumbnails"
    target = cache / f"{document.sha256}-v{VERSION}.webp"
    if target.is_file():
        return target
    try:
        with storage.open(document.stored_path) as stream:
            data = stream.read()
        image = _render(data, document.mime_type or "")
    except Exception:  # broken or unusual files simply have no thumbnail
        log.info("No thumbnail for document %s", document.id, exc_info=True)
        return None
    cache.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=cache, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            image.save(out, format="WEBP", quality=78, method=4)
        os.replace(temp, target)  # atomic: parallel requests never see half a file
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return target


def _render(data: bytes, mime: str):
    from PIL import Image, ImageOps

    if mime == "application/pdf":
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                page = pdf[0]
                scale = WIDTH / max(page.get_width(), 1)
                image = page.render(scale=scale).to_pil()
                page.close()
            finally:
                pdf.close()
    else:
        with Image.open(io.BytesIO(data)) as source:
            source.seek(0)  # first frame of a GIF or multi-page TIFF
            image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((WIDTH, MAX_HEIGHT))
    return image.convert("RGB")
