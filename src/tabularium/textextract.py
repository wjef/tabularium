"""Text extraction for full-text search: PDF text layer, OCR for scans/photos, text files.

PDFs are read page by page; only pages without a text layer go to OCR, so mixed documents
(typed pages + scanned attachments) are handled. OCR uses Tesseract when it is installed.
"""

import io
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from tabularium.models import TextStatus

log = logging.getLogger(__name__)

MIN_PAGE_CHARS = 20  # a page with less text than this is treated as a scan
IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/tiff"}
)
TEXT_TYPES = frozenset({"text/plain", "text/csv", "text/markdown"})


class OcrEngine(Protocol):
    def image_to_text(self, image) -> str: ...


@dataclass
class Extraction:
    status: TextStatus
    text: str = ""
    method: str | None = None  # text | ocr | mixed
    pages: int | None = None
    ocr_pages: int = 0
    error: str | None = None


# --- Tesseract ----------------------------------------------------------------------------

_DEFAULT_LOCATIONS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
]


def find_tesseract(configured: str = "") -> str | None:
    if configured:
        return configured if Path(configured).exists() else None
    found = shutil.which("tesseract")
    if found:
        return found
    return next((str(p) for p in _DEFAULT_LOCATIONS if p.exists()), None)


@dataclass
class OcrStatus:
    available: bool
    command: str | None = None
    languages: list[str] | None = None  # installed language packs
    missing: list[str] | None = None  # requested but not installed
    message: str = ""


def tesseract_status(configured: str, wanted: str) -> OcrStatus:
    command = find_tesseract(configured)
    if command is None:
        return OcrStatus(False, message="Tesseract is not installed (or not found).")
    try:
        out = subprocess.run(  # noqa: S603 — fixed arguments, path found above
            [command, "--list-langs"], capture_output=True, text=True, timeout=30, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return OcrStatus(False, command, message=f"Tesseract could not be started: {exc}")
    installed = sorted(line.strip() for line in out.stdout.splitlines()[1:] if line.strip())
    missing = [lang for lang in wanted.split("+") if lang and lang not in installed]
    if missing:
        return OcrStatus(
            False,
            command,
            installed,
            missing,
            f"Tesseract language pack(s) missing: {', '.join(missing)}.",
        )
    return OcrStatus(True, command, installed, [], "")


class TesseractOcr:
    def __init__(self, command: str, languages: str) -> None:
        import pytesseract

        self._pytesseract = pytesseract
        pytesseract.pytesseract.tesseract_cmd = command
        self.languages = languages

    def image_to_text(self, image) -> str:
        return self._pytesseract.image_to_string(image, lang=self.languages)


# --- extraction -------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _pdf(source, ocr: OcrEngine | None, dpi: int, max_pages: int) -> Extraction:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(source)
    try:
        total = len(pdf)
        parts, text_pages, ocr_pages, scans_without_ocr = [], 0, 0, 0
        for index in range(min(total, max_pages)):
            page = pdf[index]
            textpage = page.get_textpage()
            text = textpage.get_text_bounded() or ""
            textpage.close()
            if len(text.strip()) >= MIN_PAGE_CHARS:
                parts.append(text)
                text_pages += 1
            elif ocr is not None:
                image = page.render(scale=dpi / 72).to_pil()
                parts.append(ocr.image_to_text(image))
                ocr_pages += 1
            else:
                scans_without_ocr += 1
            page.close()
    finally:
        pdf.close()

    content = "\n\f\n".join(p.strip() for p in parts if p.strip())
    method = "mixed" if text_pages and ocr_pages else ("ocr" if ocr_pages else "text")
    if scans_without_ocr:
        return Extraction(TextStatus.NEEDS_OCR, content, method, total, ocr_pages)
    status = TextStatus.DONE if content else TextStatus.EMPTY
    return Extraction(status, content, method if content else None, total, ocr_pages)


def extract(
    stream: BinaryIO,
    mime_type: str | None,
    *,
    ocr: OcrEngine | None,
    dpi: int = 300,
    max_pages: int = 300,
) -> Extraction:
    """Extract searchable text. Never raises for broken files: returns FAILED instead."""
    mime = mime_type or ""
    try:
        data = stream.read()
        if mime == "application/pdf":
            return _pdf(data, ocr, dpi, max_pages)
        if mime in IMAGE_TYPES:
            if ocr is None:
                return Extraction(TextStatus.NEEDS_OCR, pages=1)
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                frames = getattr(image, "n_frames", 1)  # multi-page TIFF
                texts = []
                for frame in range(min(frames, max_pages)):
                    image.seek(frame)
                    texts.append(ocr.image_to_text(image.convert("RGB")))
            text = "\n\f\n".join(t.strip() for t in texts if t.strip())
            status = TextStatus.DONE if text else TextStatus.EMPTY
            return Extraction(status, text, "ocr" if text else None, frames, frames)
        if mime in TEXT_TYPES:
            text = _decode(data).strip()
            return Extraction(TextStatus.DONE if text else TextStatus.EMPTY, text, "text", 1)
        return Extraction(TextStatus.EMPTY, error="file type without extractable text")
    except Exception as exc:  # noqa: BLE001 — a broken file must not stop indexing
        log.warning("Text extraction failed: %s", exc)
        return Extraction(TextStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
