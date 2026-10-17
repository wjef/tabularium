import hashlib
import io
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.models import Document, DocumentTag, EntityType, Tag
from tabularium.services.documents import create_document
from tabularium.services.entities import create_entity
from tabularium.services.tags import create_tag, find_by_path, get_or_create_entity_tag
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


@pytest.fixture
def archive(config, session_factory):
    """Put a file into the archive and record it, like the ingest does."""
    storage = LocalStorage(config.paths.archive)

    def add(name: str, data: bytes, mime: str | None) -> str:
        staged = storage.stage(io.BytesIO(data))
        key = storage_key(staged.sha256, "." + name.rsplit(".", 1)[-1])
        storage.commit(staged, key)
        with session_factory() as s, s.begin():
            doc = create_document(
                s,
                sha256=hashlib.sha256(data).hexdigest(),
                stored_path=key,
                original_filename=name,
                size_bytes=len(data),
                mime_type=mime,
                retention_years=5,
            )
            return str(doc.id)

    return add


@pytest.fixture
def tags(session_factory):
    with session_factory() as s, s.begin():
        sender = create_tag(s, "sender")
        category = create_tag(s, "category")
        taxes = create_tag(s, "taxes", parent=category)
        create_tag(s, "PIT/2024", parent=taxes)  # a name containing the separator
        anna = create_entity(s, EntityType.PERSON, "Anna Nowak")
        return {"sender": str(sender.id), "taxes": str(taxes.id), "anna": str(anna.id)}


def doc_tags(session_factory, doc_id: str) -> set[str]:
    with session_factory() as s:
        rows = s.scalars(
            select(Tag.name).join(DocumentTag).where(DocumentTag.document_id == uuid.UUID(doc_id))
        )
        return set(rows)


# --- services -------------------------------------------------------------------------


def test_find_by_path_is_lenient_and_handles_slashes_in_names(session_factory, tags):
    with session_factory() as s:
        assert find_by_path(s, "Category / TAXES").name == "taxes"
        assert find_by_path(s, "category/taxes/pit/2024").name == "PIT/2024"
        assert find_by_path(s, "category / nothing") is None
        assert find_by_path(s, "  ") is None


def test_entity_tag_is_reused(session_factory, tags):
    with session_factory() as s, s.begin():
        sender = s.get(Tag, uuid.UUID(tags["sender"]))
        from tabularium.models import Entity

        anna = s.get(Entity, uuid.UUID(tags["anna"]))
        first = get_or_create_entity_tag(s, sender, anna)
        assert get_or_create_entity_tag(s, sender, anna).id == first.id


# --- inbox and tagging -----------------------------------------------------------------


def test_inbox_lists_untagged_and_tagging_removes_from_it(client, archive, tags, session_factory):
    first = archive("invoice.pdf", b"%PDF-1.4 one", "application/pdf")
    second = archive("scan.jpg", b"jpeg", "image/jpeg")

    r = client.get("/inbox")
    assert "invoice.pdf" in r.text and "scan.jpg" in r.text
    assert "2 document(s) without tags." in r.text

    r = client.post(
        f"/documents/{first}/tags/add", data={"tag_path": "category / taxes", "view": "inbox"}
    )
    assert "Tag added." in r.text
    assert 'hx-swap-oob="true"' in r.text  # inbox list refreshed alongside
    assert "1 document(s) without tags." in r.text
    assert f"/inbox?id={second}" in r.text  # next document link
    assert doc_tags(session_factory, first) == {"taxes"}


def test_unknown_path_and_duplicate_tag_show_errors(client, archive, tags):
    doc = archive("a.pdf", b"a", "application/pdf")
    r = client.post(f"/documents/{doc}/tags/add", data={"tag_path": "nope"})
    assert "No tag" in r.text
    client.post(f"/documents/{doc}/tags/add", data={"tag_path": "sender"})
    r = client.post(f"/documents/{doc}/tags/add", data={"tag_path": "sender"})
    assert "already has this tag" in r.text


def test_add_entity_and_new_tag_then_remove(client, archive, tags, session_factory):
    doc = archive("a.pdf", b"a", "application/pdf")
    client.post(
        f"/documents/{doc}/tags/entity",
        data={"parent_id": tags["sender"], "entity_id": tags["anna"]},
    )
    client.post(f"/documents/{doc}/tags/new", data={"parent_id": tags["taxes"], "name": "VAT"})
    assert doc_tags(session_factory, doc) == {"Anna Nowak", "VAT"}

    with session_factory() as s:
        vat = s.scalars(select(Tag).where(Tag.name == "VAT")).one()
    r = client.post(f"/documents/{doc}/tags/{vat.id}/remove")
    assert "Tag removed." in r.text
    assert doc_tags(session_factory, doc) == {"Anna Nowak"}
    assert "TAG_REMOVE" in r.text  # history shown


def test_details_and_retention(client, archive, session_factory):
    doc = archive("a.pdf", b"a", "application/pdf")
    r = client.post(
        f"/documents/{doc}/details",
        data={"title": "Invoice 03/2025", "document_date": "2025-03-14", "retain_auto": "1"},
    )
    assert "Details saved." in r.text
    with session_factory() as s:
        d = s.get(Document, uuid.UUID(doc))
        assert (d.title, d.document_date, d.retain_until) == (
            "Invoice 03/2025",
            date(2025, 3, 14),
            date(2030, 12, 31),
        )
        assert not d.retain_until_manual

    client.post(
        f"/documents/{doc}/details",
        data={
            "title": "Invoice 03/2025",
            "document_date": "2025-03-14",
            "retain_until": "2040-01-01",
        },
    )
    with session_factory() as s:
        d = s.get(Document, uuid.UUID(doc))
        assert d.retain_until == date(2040, 1, 1) and d.retain_until_manual

    r = client.post(f"/documents/{doc}/details", data={"document_date": "14.03.2025"})
    assert "not a valid date" in r.text


def test_bulk_tagging(client, archive, tags, session_factory):
    a = archive("a.pdf", b"a", "application/pdf")
    b = archive("b.pdf", b"b", "application/pdf")
    c = archive("c.pdf", b"c", "application/pdf")
    r = client.post("/documents/bulk-tag", data={"doc_ids": [a, b], "tag_path": "sender"})
    assert "Tag added to 2 document(s)." in r.text
    assert doc_tags(session_factory, a) == doc_tags(session_factory, b) == {"sender"}
    assert doc_tags(session_factory, c) == set()

    r = client.post("/documents/bulk-tag", data={"tag_path": "sender"})
    assert "Select at least one document." in r.text


# --- file serving ----------------------------------------------------------------------


def test_pdf_is_served_inline_by_id(client, archive):
    doc = archive("Faktura – Urząd.pdf", b"%PDF-1.4 x", "application/pdf")
    r = client.get(f"/documents/{doc}/file")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 x"
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline;")
    assert (
        "filename*=UTF-8''Faktura%20%E2%80%93%20Urz%C4%85d.pdf" in r.headers["content-disposition"]
    )
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_flag_forces_attachment(client, archive):
    doc = archive("a.pdf", b"%PDF", "application/pdf")
    r = client.get(f"/documents/{doc}/file?download=1")
    assert r.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("name", "mime"),
    [("page.html", "text/html"), ("logo.svg", "image/svg+xml"), ("x.docx", None)],
)
def test_active_or_unknown_types_are_never_inline(client, archive, name, mime):
    doc = archive(name, b"<script>alert(1)</script>", mime)
    r = client.get(f"/documents/{doc}/file")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-type"] == "application/octet-stream"

    page = client.get(f"/documents/{doc}")
    assert "<iframe" not in page.text and "No preview" in page.text


def test_unknown_document_and_missing_file_give_404(client, archive, config):
    assert client.get(f"/documents/{uuid.uuid4()}").status_code == 404
    doc = archive("gone.pdf", b"gone", "application/pdf")
    sha = hashlib.sha256(b"gone").hexdigest()
    (config.paths.archive / sha[:2] / f"{sha}.pdf").unlink()
    assert client.get(f"/documents/{doc}/file").status_code == 404


def test_document_page_shows_preview_and_history(client, archive):
    doc = archive("a.pdf", b"%PDF", "application/pdf")
    r = client.get(f"/documents/{doc}")
    assert f'<iframe src="/documents/{doc}/file"' in r.text
    assert "CREATE" in r.text


def test_tagging_requires_csrf(config, session_factory, archive):
    doc = archive("a.pdf", b"a", "application/pdf")
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    assert plain.post(f"/documents/{doc}/tags/add", data={"tag_path": "x"}).status_code == 403
    assert plain.post("/documents/bulk-tag", data={"tag_path": "x"}).status_code == 403


def test_inbox_error_is_shown_once(client, archive, tags):
    doc = archive("a.pdf", b"a", "application/pdf")
    r = client.post(f"/documents/{doc}/tags/add", data={"tag_path": "nope", "view": "inbox"})
    assert r.text.count("No tag &#39;nope&#39;") == 1


def test_exact_path_wins_and_ambiguous_paths_are_refused(session_factory, tags):
    with session_factory() as s, s.begin():
        taxes = find_by_path(s, "category / taxes")
        pit = create_tag(s, "PIT", parent=taxes)
        create_tag(s, "2024", parent=pit)  # "category / taxes / PIT / 2024"
    with session_factory() as s:
        # exact display paths pick the right tag
        assert find_by_path(s, "category / taxes / PIT/2024").name == "PIT/2024"
        assert find_by_path(s, "category / taxes / PIT / 2024").name == "2024"
        # spacing-insensitive input that matches both is refused
        from tabularium.services import DomainError

        with pytest.raises(DomainError, match="ambiguous"):
            find_by_path(s, "category/taxes/PIT/2024")


def test_history_links_to_documents(client, archive):
    doc = archive("a.pdf", b"a", "application/pdf")
    r = client.get("/history")
    assert f'<a href="/documents/{doc}">a.pdf</a>' in r.text
