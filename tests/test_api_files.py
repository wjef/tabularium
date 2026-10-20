"""Tagging services and how archived files are handed out (by id, safe types inline only)."""

import hashlib
import io
import uuid

import pytest
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.models import DocumentTag, Tag
from tabularium.services.documents import create_document
from tabularium.services.entities import create_entity
from tabularium.services.tags import create_tag, find_by_path, get_or_create_entity_tag
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"
API = "/api/v1"


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
        anna = create_entity(s, etype(s, "Person"), "Anna Nowak")
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


# --- tagging through the API -----------------------------------------------------------


def test_add_entity_and_new_tag_then_remove(client, archive, tags, session_factory):
    doc = archive("a.pdf", b"a", "application/pdf")
    client.post(
        f"{API}/documents/{doc}/tags", json={"parent_id": tags["sender"], "entity_id": tags["anna"]}
    )
    client.post(f"{API}/documents/{doc}/tags", json={"parent_id": tags["taxes"], "name": "VAT"})
    assert doc_tags(session_factory, doc) == {"Anna Nowak", "VAT"}

    with session_factory() as s:
        vat = s.scalars(select(Tag).where(Tag.name == "VAT")).one()
    r = client.delete(f"{API}/documents/{doc}/tags/{vat.id}")
    assert [t["name"] for t in r.json()["tags"]] == ["Anna Nowak"]
    assert doc_tags(session_factory, doc) == {"Anna Nowak"}
    history = client.get(f"{API}/documents/{doc}/history").json()
    assert "TAG_REMOVE" in [h["action"] for h in history]


def test_duplicate_tag_and_bad_date_are_refused(client, archive, tags):
    doc = archive("a.pdf", b"a", "application/pdf")
    client.post(f"{API}/documents/{doc}/tags", json={"tag_id": tags["sender"]})
    r = client.post(f"{API}/documents/{doc}/tags", json={"tag_id": tags["sender"]})
    assert r.status_code == 409 and "already has this tag" in r.json()["detail"]
    r = client.patch(f"{API}/documents/{doc}", json={"document_date": "14.03.2025"})
    assert r.status_code == 422


# --- file serving ----------------------------------------------------------------------


def test_pdf_is_served_inline_by_id(client, archive):
    doc = archive("Faktura – Urząd.pdf", b"%PDF-1.4 x", "application/pdf")
    r = client.get(f"{API}/documents/{doc}/file")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 x"
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline;")
    # handed out under the new name (hash, no title yet), never the original name
    sha = hashlib.sha256(b"%PDF-1.4 x").hexdigest()
    assert f"filename*=UTF-8''{sha}.pdf" in r.headers["content-disposition"]
    assert "Faktura" not in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_uses_date_and_title_once_set(client, archive):
    doc = archive("IMG_20261017_scan.pdf", b"%PDF y", "application/pdf")
    client.patch(
        f"{API}/documents/{doc}", json={"title": "Umowa najmu", "document_date": "2026-10-01"}
    )
    r = client.get(f"{API}/documents/{doc}/file?download=1")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''2026-10-01%20Umowa%20najmu.pdf" in disposition
    assert "IMG_" not in disposition


def test_download_flag_forces_attachment(client, archive):
    doc = archive("a.pdf", b"%PDF", "application/pdf")
    r = client.get(f"{API}/documents/{doc}/file?download=1")
    assert r.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("name", "mime"),
    [("page.html", "text/html"), ("logo.svg", "image/svg+xml"), ("x.docx", None)],
)
def test_active_or_unknown_types_are_never_inline(client, archive, name, mime):
    doc = archive(name, b"<script>alert(1)</script>", mime)
    r = client.get(f"{API}/documents/{doc}/file")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-type"] == "application/octet-stream"
    assert client.get(f"{API}/documents/{doc}").json()["preview"] == "none"


def test_unknown_document_and_missing_file_give_404(client, archive, config):
    assert client.get(f"{API}/documents/{uuid.uuid4()}").status_code == 404
    assert client.get(f"{API}/documents/{uuid.uuid4()}/file").status_code == 404
    doc = archive("gone.pdf", b"gone", "application/pdf")
    sha = hashlib.sha256(b"gone").hexdigest()
    (config.paths.archive / sha[:2] / f"{sha}.pdf").unlink()
    assert client.get(f"{API}/documents/{doc}/file").status_code == 404


def test_tagging_requires_csrf(config, session_factory, archive):
    doc = archive("a.pdf", b"a", "application/pdf")
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    assert plain.post(f"{API}/documents/{doc}/tags", json={"name": "x"}).status_code == 403
    body = {"document_ids": [doc], "tag_id": str(uuid.uuid4())}
    assert plain.post(f"{API}/documents/bulk-tags", json=body).status_code == 403


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
    entries = client.get(f"{API}/history").json()
    sha = hashlib.sha256(b"a").hexdigest()
    created = next(e for e in entries if e["object_id"] == doc)
    assert (created["label"], created["link"]) == (f"{sha}.pdf", f"/?doc={doc}")
