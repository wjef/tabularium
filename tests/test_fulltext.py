"""Full-text search: extraction (text layer, OCR, text files), index, search, snippets."""

import io
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tabularium import textextract
from tabularium.models import DocumentText, TextStatus
from tabularium.outdated import OutdatedStore
from tabularium.services.documents import create_document, set_retain_until
from tabularium.services.outdating import outdate, restore_to_active
from tabularium.services.search import SearchQuery, search
from tabularium.storage import LocalStorage, storage_key
from tabularium.textextract import extract
from tabularium.textindex import TextIndexer, snippet, status_counts
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


def make_pdf(pages: list[str | None]) -> bytes:
    """Minimal PDF; a page given as None has no text layer (like a scan)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    for text in pages:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        stream_no = len(objects) + 1
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {stream_no} 0 R "
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            "/BaseFont /Helvetica >> >> >> >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out


def make_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeOcr:
    def __init__(self, text="Zażółć gęślą jaźń — skan umowy"):
        self.text, self.calls = text, 0

    def image_to_text(self, image) -> str:
        self.calls += 1
        return self.text


LINE = "Invoice number FV 03/2025 for electricity supply"


# --- extraction -------------------------------------------------------------------------


def test_pdf_text_layer_is_read_without_ocr():
    ocr = FakeOcr()
    result = extract(io.BytesIO(make_pdf([LINE])), "application/pdf", ocr=ocr)
    assert result.status == TextStatus.DONE and "FV 03/2025" in result.text
    assert result.method == "text" and result.pages == 1 and ocr.calls == 0


def test_scanned_pages_go_to_ocr_and_mixed_documents_work():
    ocr = FakeOcr()
    result = extract(io.BytesIO(make_pdf([LINE, None])), "application/pdf", ocr=ocr)
    assert result.method == "mixed" and result.ocr_pages == 1 and ocr.calls == 1
    assert "electricity" in result.text and "gęślą" in result.text


def test_scans_wait_for_ocr_when_it_is_not_available():
    result = extract(io.BytesIO(make_pdf([None])), "application/pdf", ocr=None)
    assert result.status == TextStatus.NEEDS_OCR
    assert extract(io.BytesIO(make_png()), "image/png", ocr=None).status == TextStatus.NEEDS_OCR


def test_images_are_read_by_ocr():
    result = extract(io.BytesIO(make_png()), "image/png", ocr=FakeOcr())
    assert result.status == TextStatus.DONE and result.method == "ocr"


def test_text_files_utf8_and_windows_1250():
    polish = "Zażółć gęślą jaźń"
    for encoding in ("utf-8", "cp1250"):
        result = extract(io.BytesIO(polish.encode(encoding)), "text/plain", ocr=None)
        assert result.text == polish


def test_unsupported_and_broken_files():
    assert extract(io.BytesIO(b"PK.."), None, ocr=None).status == TextStatus.EMPTY
    broken = extract(io.BytesIO(b"%PDF-1.4 garbage"), "application/pdf", ocr=None)
    assert broken.status == TextStatus.FAILED and broken.error


def test_snippet_keeps_original_spelling():
    content = "Umowa najmu.\nWynajmujący: Jan Kowalski. Kaucja zwrotna płatna do końca miesiąca."
    piece = snippet(content, ["platna", "konca"])
    assert "płatna do końca" in piece
    assert snippet(content, ["nothing"]) is None


# --- index + search -----------------------------------------------------------------------


def archive_file(session, storage, name, data, mime):
    staged = storage.stage(io.BytesIO(data))
    key = storage_key(staged.sha256, os.path.splitext(name)[1])
    storage.commit(staged, key)
    return create_document(
        session,
        sha256=staged.sha256,
        stored_path=key,
        original_filename=name,
        size_bytes=len(data),
        mime_type=mime,
        retention_years=5,
    )


@pytest.fixture
def docs(session, config):
    storage = LocalStorage(config.paths.archive)
    d = {
        "invoice": archive_file(session, storage, "inv.pdf", make_pdf([LINE]), "application/pdf"),
        "scan": archive_file(session, storage, "scan.pdf", make_pdf([None]), "application/pdf"),
        "note": archive_file(
            session, storage, "note.txt", "Kaucja zwrotna płatna".encode("cp1250"), "text/plain"
        ),
    }
    session.commit()
    return d | {"storage": storage}


def names(session, text):
    return {d.original_filename for d in search(session, SearchQuery(text=text))[0]}


def test_indexer_extracts_and_search_finds_words_inside(session_factory, session, docs):
    indexer = TextIndexer(session_factory, docs["storage"], ocr_factory=lambda: None)
    assert indexer.run_until_idle() == 3
    assert indexer.run_until_idle() == 0  # nothing left
    counts = status_counts(session)
    assert counts["DONE"] == 2 and counts["NEEDS_OCR"] == 1 and counts["PENDING"] == 0

    assert names(session, "electricity") == {"inv.pdf"}
    assert names(session, "electr") == {"inv.pdf"}  # word prefix
    assert names(session, "PLATNA kaucja") == {"note.txt"}  # case + diacritics ignored
    assert names(session, 'fv "03') == {"inv.pdf"}  # quotes cannot break the query
    assert names(session, "skan") == set()


def test_documents_waiting_for_ocr_are_read_once_it_appears(session_factory, session, docs):
    holder = {"ocr": None}
    indexer = TextIndexer(session_factory, docs["storage"], ocr_factory=lambda: holder["ocr"])
    indexer.run_until_idle()
    holder["ocr"] = FakeOcr()
    assert indexer.refresh_ocr()
    assert indexer.run_until_idle() == 1
    assert names(session, "zazolc gesla") == {"scan.pdf"}
    row = session.get(DocumentText, docs["scan"].id)
    session.refresh(row)
    assert row.status == TextStatus.DONE and row.method == "ocr"


def test_outdating_removes_text_and_restore_reindexes(
    session_factory, session, docs, tmp_path, config
):
    indexer = TextIndexer(session_factory, docs["storage"], ocr_factory=lambda: None)
    indexer.run_until_idle()
    invoice = docs["invoice"]
    set_retain_until(session, invoice, date(2020, 1, 1), retention_years=5)
    session.commit()
    store = OutdatedStore(tmp_path / "o.db", config.paths.outdated)
    try:
        outdate(session, docs["storage"], store, invoice, today=date(2026, 1, 1))
        assert names(session, "electricity") == set()
        restore_to_active(session, docs["storage"], store, invoice.id)
        assert names(session, "electricity") == set()  # not indexed yet…
        indexer.run_until_idle()
        assert names(session, "electricity") == {"inv.pdf"}  # …until the indexer ran
    finally:
        store.dispose()


# --- web --------------------------------------------------------------------------------


@pytest.fixture
def client(config, session_factory, monkeypatch):
    monkeypatch.setattr(
        "tabularium.web.app.tesseract_status",
        lambda *a: textextract.OcrStatus(False, message="Tesseract is not installed."),
    )
    monkeypatch.setattr(
        "tabularium.web.routes_maintenance.tesseract_status",
        lambda *a: textextract.OcrStatus(False, message="Tesseract is not installed."),
    )
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_search_page_shows_snippets_and_document_page_shows_text(client, docs):
    client.app.state.text_indexer.run_until_idle()
    r = client.get("/search", params={"q": "platna"})
    assert "1 document(s) found." in r.text
    assert 'class="snippet">Kaucja zwrotna płatna</small>' in r.text

    page = client.get(f"/documents/{docs['note'].id}").text
    assert "Show extracted text" in page and "text layer" in page
    scan_page = client.get(f"/documents/{docs['scan'].id}").text
    assert "needs OCR" in scan_page

    r = client.post(f"/documents/{docs['note'].id}/text/extract")
    assert "Text extraction queued." in r.text and "Waiting for text extraction" in r.text


def test_maintenance_shows_index_and_how_to_install_ocr(client, docs):
    client.app.state.text_indexer.run_until_idle()
    page = client.get("/maintenance").text
    assert "Full-text search" in page and "Tesseract is not installed." in page
    assert "winget install --id UB-Mannheim.TesseractOCR" in page


# --- real Tesseract (only where installed with Polish) ----------------------------------


_status = textextract.tesseract_status("", "pol+eng")


@pytest.mark.skipif(not _status.available, reason=_status.message or "Tesseract not available")
def test_real_tesseract_reads_polish():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1400, 200), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        pytest.skip("no TrueType font for the test image")
    ImageDraw.Draw(image).text((20, 50), "Urząd Skarbowy zażółć", fill="black", font=font)
    text = textextract.TesseractOcr(_status.command, "pol+eng").image_to_text(image)
    assert "Urząd Skarbowy" in text
