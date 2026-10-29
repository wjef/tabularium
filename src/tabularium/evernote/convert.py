"""Turning parts of an Evernote note into PDFs: photos into one PDF, text notes printed.

Photos are placed in the PDF without re-encoding: JPEG data is embedded as it is (rotated
by its EXIF orientation through the page, not the pixels); other image formats are stored
losslessly. Text notes are printed by Microsoft Edge in headless mode.
"""

import contextlib
import html
import io
import logging
import os
import shutil
import subprocess  # noqa: S404 — runs Edge with fixed arguments
import tempfile
import time
from pathlib import Path
from typing import Protocol

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from tabularium.textextract import PDFIUM_LOCK

log = logging.getLogger(__name__)

# Pages fit A4 (in points) in the photo's own orientation, so a large photo is not printed
# as a poster-sized page; the full resolution stays in the file.
A4 = (595.0, 842.0)
# EXIF orientation → clockwise page rotation. Mirrored orientations (2, 4, 5, 7) are rare
# for photos and are shown unmirrored.
_ROTATION = {3: 180, 6: 90, 8: 270, 4: 180, 5: 270, 7: 90}


class ConversionError(Exception):
    pass


def is_photo(data: bytes, mime: str) -> bool:
    """Can this attachment go into a photo PDF? (Formats Pillow cannot read, such as HEIC,
    are kept as they are.)"""
    if not mime.startswith("image/") or mime == "image/svg+xml":
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception:  # noqa: BLE001 — anything unreadable is kept as an attachment
        return False
    return True


def _page_size(width: int, height: int) -> tuple[float, float]:
    short, long = A4
    box = (short, long) if height >= width else (long, short)
    scale = min(box[0] / width, box[1] / height)
    return width * scale, height * scale


def photos_to_pdf(photos: list[bytes]) -> bytes:
    """One page per photo, in the given order."""
    if not photos:
        raise ConversionError("No photos to convert.")
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument.new()
        try:
            for data in photos:
                _add_photo(pdf, data)
            out = io.BytesIO()
            pdf.save(out)
            return out.getvalue()
        finally:
            pdf.close()


def _add_photo(pdf: pdfium.PdfDocument, data: bytes) -> None:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        is_jpeg = image.format in ("JPEG", "MPO")  # MPO: a JPEG with extra frames (phones)
        orientation = image.getexif().get(0x0112, 1)
        if not is_jpeg:
            flat = ImageOps.exif_transpose(image)  # lossless: pixels only move
            if flat.mode not in ("RGB", "L"):
                rgba = flat.convert("RGBA")
                flat = Image.new("RGB", rgba.size, "white")
                flat.paste(rgba, mask=rgba.getchannel("A"))
            width, height = flat.size
    page_w, page_h = _page_size(width, height)
    page = pdf.new_page(page_w, page_h)
    obj = pdfium.PdfImage.new(pdf)
    if is_jpeg:
        obj.load_jpeg(io.BytesIO(data), inline=True)
    else:
        obj.set_bitmap(pdfium.PdfBitmap.from_pil(flat))
    obj.set_matrix(pdfium.PdfMatrix().scale(page_w, page_h))
    page.insert_obj(obj)
    page.gen_content()
    if is_jpeg and _ROTATION.get(orientation):
        page.set_rotation(_ROTATION[orientation])
    page.close()


# --- printing text notes -----------------------------------------------------------------

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
@page {{ size: A4; margin: 18mm 16mm; }}
body {{ font: 11pt/1.5 "Segoe UI", Arial, sans-serif; color: #111; }}
h1.title {{ font-size: 16pt; margin: 0 0 12pt; }}
table {{ border-collapse: collapse; }}
td, th {{ border: 1px solid #bbb; padding: 2pt 5pt; vertical-align: top; }}
pre {{ white-space: pre-wrap; }}
</style></head>
<body><h1 class="title">{title}</h1>{body}</body></html>
"""


def note_page(title: str, body_html: str) -> str:
    """The printable page; ``body_html`` must already be safe (``enex.parse_body``)."""
    return _PAGE.format(title=html.escape(title), body=body_html)


class Printer(Protocol):
    available: bool

    def print_html(self, page: str) -> bytes: ...


def find_edge() -> Path | None:
    found = shutil.which("msedge")
    candidates = [found] if found else []
    for root in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if base := os.environ.get(root):
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    return next((Path(c) for c in candidates if c and os.path.isfile(c)), None)


class EdgePrinter:
    """Prints with Microsoft Edge, headless, using a throwaway profile."""

    def __init__(self, edge: Path | None = None, timeout: float = 90.0) -> None:
        self.edge = edge or find_edge()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self.edge is not None

    def print_html(self, page: str) -> bytes:
        if self.edge is None:
            raise ConversionError("Microsoft Edge was not found; it is needed to print text notes.")
        with tempfile.TemporaryDirectory(
            prefix="tabularium-print-", ignore_cleanup_errors=True
        ) as tmp:
            source, target = Path(tmp) / "note.html", Path(tmp) / "note.pdf"
            source.write_text(page, encoding="utf-8")
            args = [
                str(self.edge),
                "--headless=new",
                "--disable-gpu",
                "--disable-extensions",
                "--no-first-run",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={target}",
                source.as_uri(),
            ]
            deadline = time.monotonic() + self.timeout
            try:
                subprocess.run(args, capture_output=True, timeout=self.timeout, check=False)  # noqa: S603
            except subprocess.TimeoutExpired as exc:
                raise ConversionError("Edge did not finish printing the note.") from exc
            # The Edge launcher may hand the work to another process and return early.
            data = _wait_for_pdf(target, deadline)
            time.sleep(0.5)  # let Edge release the profile before it is removed
            return data


def _wait_for_pdf(path: Path, deadline: float) -> bytes:
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            data = path.read_bytes()
            if data.rstrip().endswith(b"%%EOF"):
                return data
        time.sleep(0.2)
    raise ConversionError("Edge did not finish printing the note.")
