"""Import from Evernote (ENEX): photos merged into a PDF, text notes printed, attachments
kept unchanged and linked, nothing imported twice."""

import base64
import hashlib
import io
import os
from pathlib import Path
from xml.sax.saxutils import escape

import pypdfium2 as pdfium
import pytest
from PIL import Image
from sqlalchemy import select

from tabularium.evernote.convert import (
    ConversionError,
    EdgePrinter,
    find_edge,
    is_photo,
    note_page,
    photos_to_pdf,
)
from tabularium.evernote.enex import Note, parse_body, read_notes
from tabularium.evernote.importer import EvernoteImporter, notebook_of, plan_note
from tabularium.fsutil import lp
from tabularium.ingest import Ingestor
from tabularium.models import Document, DocumentRelation
from tabularium.storage import LocalStorage


def jpeg(color: str, size=(40, 30), orientation: int | None = None) -> bytes:
    out = io.BytesIO()
    image = Image.new("RGB", size, color)
    exif = image.getexif()
    if orientation:
        exif[0x0112] = orientation
    image.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def png(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (20, 20), color).save(out, "PNG")
    return out.getvalue()


def pdf_bytes(pages: int = 1) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(200, 300)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def note_xml(title: str, body: str, resources=(), tags=(), created="20210304T101500Z") -> str:
    """One ENEX <note>; ``resources`` are (bytes, mime, file name or None)."""
    media = "".join(f'<en-media hash="{md5(d)}" type="{m}"/>' for d, m, _ in resources)
    enml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        f"<en-note>{body}{media}</en-note>"
    )
    parts = [f"<note><title>{escape(title)}</title><content><![CDATA[{enml}]]></content>"]
    parts.append(f"<created>{created}</created>")
    parts += [f"<tag>{escape(t)}</tag>" for t in tags]
    for data, mime, name in resources:
        attrs = f"<resource-attributes><file-name>{escape(name)}</file-name>" if name else ""
        parts.append(
            f'<resource><data encoding="base64">{base64.b64encode(data).decode()}</data>'
            f"<mime>{mime}</mime>{attrs and attrs + '</resource-attributes>'}</resource>"
        )
    return "".join(parts) + "</note>"


def enex(*notes: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">\n'
        f'<en-export export-date="20261001T120000Z" application="Evernote">{"".join(notes)}'
        "</en-export>"
    ).encode()


def put(folder: Path, relpath: str, data: bytes) -> Path:
    path = folder / relpath
    os.makedirs(lp(path.parent), exist_ok=True)
    path.write_bytes(data)
    return path


class FakePrinter:
    """Stands in for Edge: a different (valid) PDF on every call, like a real print."""

    available = True

    def __init__(self, fail_on: str | None = None) -> None:
        self.pages: list[str] = []
        self.fail_on = fail_on

    def print_html(self, page: str) -> bytes:
        if self.fail_on and self.fail_on in page:
            raise ConversionError("Edge did not finish printing the note.")
        self.pages.append(page)
        return pdf_bytes() + f"%{len(self.pages)}{os.urandom(4).hex()}\n".encode()


@pytest.fixture
def ingestor(config, session_factory):
    config.ingest.stable_seconds = 0
    return Ingestor(config, session_factory, LocalStorage(config.paths.archive))


@pytest.fixture
def printer():
    return FakePrinter()


@pytest.fixture
def importer(ingestor, printer):
    return EvernoteImporter(ingestor, printer)


def documents(session_factory) -> list[Document]:
    with session_factory() as s:
        return list(s.scalars(select(Document).order_by(Document.ingested_at)))


def links(session_factory) -> list[tuple]:
    with session_factory() as s:
        return [
            (r.from_document_id, r.to_document_id, r.type.value)
            for r in s.scalars(select(DocumentRelation))
        ]


# --- ENML ------------------------------------------------------------------------------


def test_body_text_keeps_lines_and_todos_and_drops_unsafe_markup():
    body = parse_body(
        '<?xml version="1.0"?><!DOCTYPE en-note SYSTEM "x"><en-note>'
        '<div style="color:red">Faktura&nbsp;nr 12/2021</div><div><br/></div>'
        '<en-todo checked="true"/>zapłacone<br/><en-todo/>wysłać'
        '<script>alert(1)</script><img src="http://example.com/pixel.gif"/> '
        '<a href="http://example.com">link</a><en-media hash="ABC" type="image/jpeg"/>'
        "<en-crypt>secret</en-crypt></en-note>"
    )
    assert body.text == "Faktura nr 12/2021\n\n☑ zapłacone\n☐ wysłać link[encrypted text]"
    assert body.media == ("abc",)
    assert "script" not in body.html and "alert" not in body.html
    assert "http" not in body.html and "style" not in body.html and "secret" not in body.html
    assert "<div>Faktura" in body.html


def test_reads_notes_tags_and_resources(tmp_path):
    photo = jpeg("red")
    path = put(
        tmp_path,
        "a.enex",
        enex(
            note_xml("Rachunek", "<div>prąd</div>", [(photo, "image/jpeg", "IMG_1.jpg")], ["FV"]),
            note_xml("Druga", "<div>tekst</div>"),
        ),
    )
    first, second = read_notes(path)
    assert first.title == "Rachunek" and first.tags == ("FV",)
    [resource] = first.resources
    assert resource.data == photo and resource.mime == "image/jpeg"
    assert resource.file_name == "IMG_1.jpg" and resource.md5 == md5(photo)
    assert second.resources == () and second.fingerprint != first.fingerprint


# --- planning and conversion -----------------------------------------------------------


def _note(body: str, resources=(), title="Umowa najmu") -> tuple[Note, object]:
    from tabularium.evernote.enex import Resource

    note = Note(title, None, (), body, tuple(Resource(d, m, n) for d, m, n in resources))
    return note, parse_body(body)


def test_plan_photos_in_note_order_then_each_attachment():
    one, two, doc = jpeg("red"), jpeg("blue"), pdf_bytes()
    body = f'<en-note><en-media hash="{md5(two)}"/><en-media hash="{md5(one)}"/></en-note>'
    note, parsed = _note(
        body,
        [
            (one, "image/jpeg", "a.jpg"),
            (doc, "application/pdf", "umowa.pdf"),
            (two, "image/jpeg", None),
        ],
    )
    photos, attachment = plan_note(note, parsed)
    assert photos.kind == "photos" and [r.data for r in photos.resources] == [two, one]
    assert photos.file_name == "Umowa najmu.pdf" and photos.title == "Umowa najmu"
    assert attachment.kind == "attachment" and attachment.file_name == "umowa.pdf"
    assert attachment.title == "Umowa najmu – umowa.pdf"


def test_plan_text_note_and_empty_note():
    note, parsed = _note("<en-note><div>Hasło do wifi: nie tutaj</div></en-note>")
    [part] = plan_note(note, parsed)
    assert part.kind == "text"
    note, parsed = _note("<en-note><div><br/></div></en-note>")
    assert plan_note(note, parsed) == []


def test_heic_and_broken_images_stay_attachments():
    assert is_photo(jpeg("red"), "image/jpeg") and is_photo(png("red"), "image/png")
    assert not is_photo(b"not really an image", "image/heic")
    assert not is_photo(b"<svg/>", "image/svg+xml")


def test_photos_pdf_is_lossless_and_follows_exif_orientation():
    upright, sideways, drawing = jpeg("red"), jpeg("green", (60, 40), orientation=6), png("blue")
    pdf = pdfium.PdfDocument(photos_to_pdf([upright, sideways, drawing]))
    assert len(pdf) == 3
    assert [pdf[i].get_rotation() for i in range(3)] == [0, 90, 0]
    [image] = [o for o in pdf[0].get_objects() if isinstance(o, pdfium.PdfImage)]
    assert image.get_filters() == ["DCTDecode"]
    assert bytes(image.get_data(decode_simple=False)) == upright  # JPEG embedded as it is
    width, height = pdf[0].get_size()
    assert max(width, height) <= 842 and width / height == pytest.approx(40 / 30)


def test_notebook_name():
    from pathlib import PurePosixPath as P

    assert notebook_of(P("Faktury.enex"), whole_notebook=True) == "Faktury"
    assert notebook_of(P("Evernote/Faktury.enex"), whole_notebook=True) == "Faktury"
    assert notebook_of(P("Faktury/Rachunek za prąd.enex"), whole_notebook=False) == "Faktury"
    assert notebook_of(P("Rachunek.enex"), whole_notebook=False) == "Rachunek"


# --- importing -------------------------------------------------------------------------


CONTRACT = pdf_bytes(2)  # once: a PDF is saved with a new /ID every time


def sample(folders) -> dict:
    photo1, photo2, contract = jpeg("red"), jpeg("blue"), CONTRACT
    put(
        folders["ingest"],
        "Dom.enex",
        enex(
            note_xml(
                "Umowa najmu",
                "<div>Podpisana z Anna Nowak</div>",
                [
                    (photo1, "image/jpeg", "IMG_1.jpg"),
                    (photo2, "image/jpeg", "IMG_2.jpg"),
                    (contract, "application/pdf", "umowa.pdf"),
                ],
                tags=["umowy", "2021"],
            ),
            note_xml("Kody do bramy", "<div>Brama: zapytać Jan Kowalski</div>"),
            note_xml("Pusta", "<div><br/></div>"),
        ),
    )
    return {"contract": contract}


def test_dry_run_counts_without_changing_anything(importer, folders, session_factory, printer):
    sample(folders)
    report = importer.run(dry_run=True)
    assert (report.files, report.notes, report.documents) == (1, 3, 3)
    assert (report.photo_pdfs, report.printed, report.attachments, report.empty) == (1, 1, 1, 1)
    assert report.failed == [] and printer.pages == []
    assert documents(session_factory) == []
    assert (folders["ingest"] / "Dom.enex").exists()


def test_import_creates_documents_to_be_tagged(importer, folders, session_factory, printer):
    files = sample(folders)
    report = importer.run(dry_run=False)

    assert (report.notes, report.documents, report.empty, report.failed) == (3, 3, 1, [])
    photos, contract, printed = documents(session_factory)
    assert photos.title == "Umowa najmu" and photos.mime_type == "application/pdf"
    assert photos.original_filename == "Umowa najmu.pdf" and photos.original_relpath == "Dom.enex"
    assert photos.note == (
        "From Evernote. Notebook: Dom. Tags: umowy, 2021.\n\nPodpisana z Anna Nowak"
    )
    assert contract.title == "Umowa najmu – umowa.pdf" and contract.original_filename == "umowa.pdf"
    assert contract.note == "From Evernote. Notebook: Dom. Tags: umowy, 2021."
    archived = folders["archive"] / contract.stored_path
    assert archived.read_bytes() == files["contract"]  # unchanged
    assert printed.title == "Kody do bramy" and printed.note == "From Evernote. Notebook: Dom."
    assert "Brama: zapytać Jan Kowalski" in printer.pages[0]
    for doc in (photos, contract, printed):
        assert doc.tagging_done_at is None  # in "To be tagged"
        assert (doc.document_date, doc.sent_date, doc.received_date) == (None, None, None)
    assert links(session_factory) == [(contract.id, photos.id, "RELATED")]
    assert (folders["ingest"] / "_processed" / "Dom.enex").exists()
    assert not (folders["ingest"] / "Dom.enex").exists()


def test_same_notes_again_are_not_imported_twice(importer, folders, session_factory):
    sample(folders)
    importer.run(dry_run=False)
    sample(folders)  # exported again, e.g. a later backup
    report = importer.run(dry_run=False)
    assert (report.already_imported, report.documents, report.empty) == (2, 0, 1)
    assert len(documents(session_factory)) == 3
    assert len(list((folders["ingest"] / "_processed").iterdir())) == 2


def test_attachment_already_archived_is_linked_not_copied(
    importer, ingestor, folders, session_factory
):
    contract = pdf_bytes(3)
    put(folders["ingest"], "umowa.pdf", contract)
    ingestor.scan()
    [existing] = documents(session_factory)
    put(
        folders["ingest"],
        "Dom/Umowa.enex",
        enex(
            note_xml(
                "Umowa",
                "",
                [(jpeg("red"), "image/jpeg", None), (contract, "application/pdf", "u.pdf")],
            )
        ),
    )
    report = importer.run(dry_run=False)
    assert (report.documents, report.duplicates) == (1, 1)
    docs = documents(session_factory)
    assert len(docs) == 2 and existing.title is None
    [new] = [d for d in docs if d.id != existing.id]
    assert new.note == "From Evernote. Notebook: Dom."
    assert links(session_factory) == [(existing.id, new.id, "RELATED")]


def test_failed_note_does_not_stop_the_others(ingestor, folders, session_factory):
    put(
        folders["ingest"],
        "Mix.enex",
        enex(note_xml("Zepsuta", "<div>a</div>"), note_xml("Dobra", "<div>b</div>")),
    )
    importer = EvernoteImporter(ingestor, FakePrinter(fail_on="Zepsuta"))
    report = importer.run(dry_run=False)
    assert report.documents == 1 and len(report.failed) == 1 and "Zepsuta" in report.failed[0]
    assert [d.title for d in documents(session_factory)] == ["Dobra"]
    assert (folders["ingest"] / "_failed" / "Mix.enex").exists()
    assert not list((folders["archive"] / ".tmp").iterdir())

    # Moved back after fixing the cause: only the failed note is imported now.
    os.replace(folders["ingest"] / "_failed" / "Mix.enex", folders["ingest"] / "Mix.enex")
    report = EvernoteImporter(ingestor, FakePrinter()).run(dry_run=False)
    assert (report.already_imported, report.documents) == (1, 1)


def test_unreadable_file_is_reported(importer, folders):
    put(folders["ingest"], "broken.enex", b"<en-export><note><title>x</title>")
    report = importer.run(dry_run=False)
    assert len(report.failed) == 1 and report.failed[0].startswith("broken.enex: ")
    assert (folders["ingest"] / "_failed" / "broken.enex").exists()


def test_ingest_scan_leaves_evernote_files_for_the_import(ingestor, folders, session_factory):
    put(folders["ingest"], "Dom.enex", enex(note_xml("Kody", "<div>a</div>")))
    report = ingestor.scan()
    assert report.evernote == ["Dom.enex"] and report.imported == []
    assert documents(session_factory) == []
    assert (folders["ingest"] / "Dom.enex").exists()


@pytest.mark.skipif(find_edge() is None, reason="Microsoft Edge is not installed")
def test_edge_prints_a_note():
    body = parse_body("<en-note><div>Zażółć gęślą jaźń</div></en-note>")
    data = EdgePrinter().print_html(note_page("Notatka", body.html))
    pdf = pdfium.PdfDocument(data)
    text = pdf[0].get_textpage().get_text_range()
    assert "Notatka" in text and "Zażółć gęślą jaźń" in text


# --- API -------------------------------------------------------------------------------


def test_api_previews_then_imports_in_the_background(config, session_factory):
    import time

    from fastapi.testclient import TestClient

    from tabularium.web.app import create_app
    from tabularium.web.security import CSRF_HEADER

    config.ingest.stable_seconds = 0
    app = create_app(config, csrf_token="t", session_factory=session_factory, scan_on_startup=False)
    app.state.evernote.importer.printer = FakePrinter()
    client = TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: "t"})
    sample({"ingest": config.paths.ingest})

    assert client.post("/api/v1/ingest/scan").json()["evernote"] == ["Dom.enex"]
    assert client.get("/api/v1/ingest/evernote").json()["files_waiting"] == 1

    def finish(dry_run: bool) -> dict:
        status = client.post("/api/v1/ingest/evernote", json={"dry_run": dry_run}).json()
        deadline = time.monotonic() + 10
        while status["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
            status = client.get("/api/v1/ingest/evernote").json()
        return status

    preview = finish(dry_run=True)
    assert preview["report"]["dry_run"] is True and preview["report"]["documents"] == 3
    assert client.get("/api/v1/info").json()["inbox"] == 0

    done = finish(dry_run=False)
    assert done["error"] is None and done["report"]["documents"] == 3
    assert done["files_waiting"] == 0 and done["files_done"] == done["files_total"] == 1
    assert client.get("/api/v1/info").json()["inbox"] == 3
