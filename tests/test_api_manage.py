"""JSON API (/api/v1): tag tree, entities, types, memberships, outdated, ingest, export,
maintenance."""

import io
import uuid
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tabularium.outdated import OutdatedStore
from tabularium.services.documents import create_document, set_retain_until
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"
API = "/api/v1"


@pytest.fixture
def store(tmp_path, config):
    s = OutdatedStore(tmp_path / "outdated.db", config.paths.outdated)
    yield s
    s.dispose()


@pytest.fixture
def client(config, session_factory, store):
    app = create_app(
        config,
        csrf_token=TOKEN,
        session_factory=session_factory,
        scan_on_startup=False,
        outdated_store=store,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def new_id(response) -> str:
    assert response.status_code == 200, response.text
    return response.json()["message"]


def test_tag_tree_management(client):
    category = new_id(client.post(f"{API}/tags", json={"name": "category"}))
    bills = new_id(client.post(f"{API}/tags", json={"name": "bills", "parent_id": category}))
    invoices = new_id(client.post(f"{API}/tags", json={"name": "invoices", "parent_id": category}))
    r = client.post(f"{API}/tags", json={"name": "CATEGORY"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]

    assert client.patch(f"{API}/tags/{bills}", json={"name": "Bills"}).status_code == 200
    assert client.patch(f"{API}/tags/{bills}", json={"move_to_top": True}).status_code == 200
    paths = {t["path"] for t in client.get(f"{API}/tags").json()}
    assert paths == {"category", "Bills", "category / invoices"}

    r = client.post(f"{API}/tags/{bills}/merge", json={"target_id": invoices})
    assert r.json()["message"] == "0"
    r = client.delete(f"{API}/tags/{category}")
    assert r.status_code == 409 and "sub-tags" in r.json()["detail"]
    assert client.delete(f"{API}/tags/{invoices}").status_code == 200
    assert client.patch(f"{API}/tags/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_entities_types_and_memberships(client):
    types = {t["name"]: t["id"] for t in client.get(f"{API}/entity-types").json()}
    bank = new_id(client.post(f"{API}/entity-types", json={"name": "Bank"}))
    jan = new_id(
        client.post(f"{API}/entities", json={"name": "Jan Kowalski", "type_id": types["Person"]})
    )
    family = new_id(
        client.post(f"{API}/entities", json={"name": "Kowalski Family", "type_id": types["Other"]})
    )
    r = client.patch(
        f"{API}/entities/{jan}",
        json={"attributes": {"tax id": "000"}, "aliases": ["JK"], "type_id": types["Person"]},
    )
    assert r.status_code == 200
    assert client.put(f"{API}/entities/{jan}/groups/{family}").status_code == 200
    r = client.put(f"{API}/entities/{family}/groups/{jan}")
    assert r.status_code == 409 and "loop" in r.json()["detail"]

    detail = client.get(f"{API}/entities/{family}").json()
    assert [m["name"] for m in detail["members"]] == ["Jan Kowalski"]
    jan_detail = client.get(f"{API}/entities/{jan}").json()
    assert jan_detail["attributes"] == {"tax id": "000"} and jan_detail["aliases"] == ["JK"]
    assert [g["name"] for g in jan_detail["groups"]] == ["Kowalski Family"]
    assert jan_detail["history"][0]["action"] == "MEMBER_ADD"

    assert client.delete(f"{API}/entities/{jan}/groups/{family}").status_code == 200
    assert (
        client.patch(f"{API}/entity-types/{bank}", json={"name": "Bank / Insurer"}).status_code
        == 200
    )
    assert client.delete(f"{API}/entity-types/{bank}").status_code == 200
    assert client.delete(f"{API}/entities/{jan}").status_code == 200


def _archive(config, session_factory, content=b"%PDF x", keep_until=date(2020, 1, 1)):
    storage = LocalStorage(config.paths.archive)
    staged = storage.stage(io.BytesIO(content))
    key = storage_key(staged.sha256, ".pdf")
    storage.commit(staged, key)
    with session_factory() as s, s.begin():
        doc = create_document(
            s,
            sha256=staged.sha256,
            stored_path=key,
            original_filename="scan.pdf",
            size_bytes=len(content),
            mime_type="application/pdf",
            retention_years=5,
        )
        set_retain_until(s, doc, keep_until, retention_years=5)
        return str(doc.id)


def test_outdate_list_detail_restore(client, config, session_factory):
    doc = _archive(config, session_factory)
    r = client.post(f"{API}/documents/{doc}/outdate", json={"reason": "old"})
    assert r.status_code == 200
    listed = client.get(f"{API}/outdated").json()
    assert [o["id"] for o in listed] == [doc] and listed[0]["reason"] == "old"
    detail = client.get(f"{API}/outdated/{doc}").json()
    assert detail["snapshot"]["tags"] == [] and detail["events"][0]["action"] == "OUTDATED"
    assert client.get(f"{API}/info").json()["outdated"] == 1
    assert client.post(f"{API}/outdated/{doc}/restore").status_code == 200
    assert client.get(f"{API}/documents/{doc}").status_code == 200


def test_ingest_scan_and_export(client, config, session_factory):
    config.ingest.stable_seconds = 0
    (config.paths.ingest / "new.pdf").write_bytes(b"%PDF new")
    r = client.post(f"{API}/ingest/scan").json()
    assert r["imported"] == ["new.pdf"]
    doc_id = client.get(f"{API}/documents").json()["items"][0]["id"]
    r = client.post(f"{API}/export", json={"document_ids": [doc_id]})
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "index.csv" in z.namelist() and len(z.namelist()) == 2


def test_maintenance(client, config, session_factory, monkeypatch):
    _archive(config, session_factory, keep_until=date(2099, 1, 1))
    status = client.get(f"{API}/maintenance").json()
    assert status["text_counts"]["PENDING"] == 1 and status["can_restore"] is False
    report = client.post(f"{API}/maintenance/check").json()
    assert report["healthy"] and report["ok"] == 1
    assert client.post(f"{API}/maintenance/metadata").status_code == 200
    assert (config.paths.archive / "metadata.json").exists()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    config.database_path.write_bytes(b"")
    assert client.post(f"{API}/maintenance/backup").json()["message"].endswith("-manual.db")
    r = client.post(f"{API}/maintenance/restore")
    assert r.status_code == 409 and "empty database" in r.json()["detail"]
