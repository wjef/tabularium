"""JSON API (/api/v1) for the single-page front end: documents, tagging, links, catalog."""

import hashlib
import io

import pytest
from conftest import etype
from fastapi.testclient import TestClient

from tabularium.services.documents import create_document
from tabularium.services.entities import create_entity
from tabularium.services.tags import create_tag
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
def data(config, session_factory):
    storage = LocalStorage(config.paths.archive)
    with session_factory() as s, s.begin():
        anna = create_entity(s, etype(s, "Person"), "Anna Nowak")
        sender = create_tag(s, "sender")
        category = create_tag(s, "category")
        taxes = create_tag(s, "taxes", parent=category)
        docs = {}
        for key, content in [("a", b"%PDF a"), ("b", b"%PDF b"), ("c", b"%PDF c")]:
            staged = storage.stage(io.BytesIO(content))
            k = storage_key(staged.sha256, ".pdf")
            storage.commit(staged, k)
            docs[key] = create_document(
                s,
                sha256=staged.sha256,
                stored_path=k,
                original_filename=f"IMG_{key}.pdf",
                size_bytes=len(content),
                mime_type="application/pdf",
                retention_years=5,
            ).id
        return {
            "docs": {k: str(v) for k, v in docs.items()},
            "anna": str(anna.id),
            "sender": str(sender.id),
            "taxes": str(taxes.id),
        }


def test_info_and_catalog(client, data):
    info = client.get(f"{API}/info").json()
    assert info["untagged"] == 3 and info["outdated"] == 0 and info["restore_pending"] is False
    tags = client.get(f"{API}/tags").json()
    assert [t["path"] for t in tags] == ["category", "sender", "category / taxes"]
    assert client.get(f"{API}/entities").json()[0]["type_name"] == "Person"
    assert "Person" in [t["name"] for t in client.get(f"{API}/entity-types").json()]
    assert {c["value"] for c in client.get(f"{API}/link-choices").json()} >= {"PAYMENT_FOR:out"}


def test_list_filters_paginates_and_names_by_hash(client, data):
    page = client.get(f"{API}/documents", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    item = page["items"][0]
    assert item["display_name"] == item["stored_name"] and item["display_name"].endswith(".pdf")
    assert item["preview"] == "pdf" and item["tags"] == [] and item["text_status"] == "PENDING"
    second = client.get(f"{API}/documents", params={"limit": 2, "offset": 2}).json()
    assert len(second["items"]) == 1
    assert client.get(f"{API}/documents", params={"sort": "nope"}).status_code == 422


def test_tagging_flow_and_untagged_filter(client, data):
    doc = data["docs"]["a"]
    r = client.post(f"{API}/documents/{doc}/tags", json={"tag_id": data["taxes"]})
    assert r.status_code == 200
    assert [t["path"] for t in r.json()["tags"]] == ["category / taxes"]
    # entity under a facet (created on demand) and a brand-new tag
    r = client.post(
        f"{API}/documents/{doc}/tags", json={"entity_id": data["anna"], "parent_id": data["sender"]}
    )
    r = client.post(f"{API}/documents/{doc}/tags", json={"name": "VAT", "parent_id": data["taxes"]})
    assert [t["path"] for t in r.json()["tags"]] == [
        "category / taxes",
        "category / taxes / VAT",
        "sender / Anna Nowak",
    ]
    # duplicate → 409 with a readable message
    r = client.post(f"{API}/documents/{doc}/tags", json={"tag_id": data["taxes"]})
    assert r.status_code == 409 and "already has this tag" in r.json()["detail"]

    untagged = client.get(f"{API}/documents", params={"untagged": True}).json()
    assert untagged["total"] == 2 and doc not in [i["id"] for i in untagged["items"]]
    by_tag = client.get(f"{API}/documents", params={"tag": data["taxes"]}).json()
    assert [i["id"] for i in by_tag["items"]] == [doc]

    r = client.delete(f"{API}/documents/{doc}/tags/{data['taxes']}")
    assert "category / taxes" not in [t["path"] for t in r.json()["tags"]]
    assert client.get(f"{API}/info").json()["untagged"] == 2


def test_update_details_and_retention(client, data):
    doc = data["docs"]["a"]
    r = client.patch(
        f"{API}/documents/{doc}", json={"title": "Umowa najmu", "document_date": "2025-03-01"}
    )
    body = r.json()
    assert body["display_name"] == "Umowa najmu" and body["retain_until"] == "2030-12-31"
    assert body["retain_until_manual"] is False
    r = client.patch(f"{API}/documents/{doc}", json={"retain_until": "2040-01-01"})
    assert r.json()["retain_until_manual"] is True
    r = client.patch(f"{API}/documents/{doc}", json={"retain_auto": True, "title": ""})
    body = r.json()
    assert body["retain_until"] == "2030-12-31" and body["title"] is None
    assert client.patch(f"{API}/documents/{doc}", json={"bogus": 1}).status_code == 422


def test_search_by_title_and_bulk_tag(client, data):
    client.patch(f"{API}/documents/{data['docs']['b']}", json={"title": "Faktura FV 03/2025"})
    r = client.get(f"{API}/documents", params={"q": "faktura"}).json()
    assert [i["title"] for i in r["items"]] == ["Faktura FV 03/2025"]
    r = client.post(
        f"{API}/documents/bulk-tags",
        json={"document_ids": list(data["docs"].values()), "tag_id": data["sender"]},
    )
    assert r.json()["message"] == "3"
    assert client.get(f"{API}/info").json()["untagged"] == 0


def test_links_and_history(client, data):
    a, b = data["docs"]["a"], data["docs"]["b"]
    r = client.post(f"{API}/documents/{a}/links", json={"other_id": b, "kind": "PAYMENT_FOR:in"})
    link = r.json()["links"][0]
    assert link["phrase"] == "is paid by" and link["direction"] == "in" and link["other"]["id"] == b
    other_side = client.get(f"{API}/documents/{b}").json()["links"][0]
    assert other_side["phrase"] == "is the payment for"
    r = client.delete(f"{API}/documents/{a}/links/{link['relation_id']}")
    assert r.json()["links"] == []
    actions = [h["action"] for h in client.get(f"{API}/documents/{a}/history").json()]
    assert actions[:2] == ["RELATION_REMOVE", "RELATION_ADD"]
    global_history = client.get(f"{API}/history", params={"limit": 5}).json()
    assert global_history[0]["label"] and global_history[0]["link"]


def test_detail_and_errors(client, data):
    doc = data["docs"]["c"]
    detail = client.get(f"{API}/documents/{doc}").json()
    assert detail["original_filename"] == "IMG_c.pdf"
    assert detail["sha256"] == hashlib.sha256(b"%PDF c").hexdigest()
    assert detail["can_outdate"] is False and detail["text"]["status"] == "PENDING"
    assert client.get(f"{API}/documents/{doc}/text").json() == {"content": None}
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"{API}/documents/{missing}").status_code == 404
    r = client.post(f"{API}/documents/{doc}/outdate", json={})
    assert r.status_code == 409 and "kept until" in r.json()["detail"]


def test_api_changes_require_csrf(config, session_factory, data):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    doc = data["docs"]["a"]
    assert plain.get(f"{API}/documents").status_code == 200  # reading is fine
    assert plain.patch(f"{API}/documents/{doc}", json={"title": "x"}).status_code == 403
    assert (
        plain.post(f"{API}/documents/{doc}/tags", json={"tag_id": data["sender"]}).status_code
        == 403
    )
