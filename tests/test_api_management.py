"""Tag tree and entity management through the JSON API: guards, audit, history labels."""

import uuid

import pytest
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.models import AuditLog, Entity, Tag
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


def type_id(session_factory, name: str) -> str:
    with session_factory() as s:
        return str(etype(s, name).id)


def new_tag(client, name: str, parent: str | None = None) -> str:
    response = client.post(f"{API}/tags", json={"name": name, "parent_id": parent})
    assert response.status_code == 200, response.text
    return response.json()["message"]  # the new tag's id


def new_entity(client, session_factory, name: str, type_name: str = "Person") -> str:
    body = {"name": name, "type_id": type_id(session_factory, type_name)}
    assert client.post(f"{API}/entities", json=body).status_code == 200
    with session_factory() as s:
        return str(s.scalars(select(Entity.id).where(Entity.name == name)).one())


def test_build_tree_and_paths(client):
    category = new_tag(client, "category")
    taxes = new_tag(client, "taxes", category)
    new_tag(client, "PIT", taxes)
    paths = {t["path"] for t in client.get(f"{API}/tags").json()}
    assert paths == {"category", "category / taxes", "category / taxes / PIT"}


def test_duplicate_name_is_refused_without_changes(client, session_factory):
    new_tag(client, "sender")
    response = client.post(f"{API}/tags", json={"name": "SENDER"})
    assert response.status_code == 409 and "already exists" in response.json()["detail"]
    with session_factory() as s:
        assert len(s.scalars(select(Tag)).all()) == 1


def test_rename_move_merge_delete(client, session_factory):
    category = new_tag(client, "category")
    bills, invoices = new_tag(client, "bills", category), new_tag(client, "invoices", category)

    assert client.patch(f"{API}/tags/{bills}", json={"name": "Bills"}).status_code == 200
    assert client.patch(f"{API}/tags/{bills}", json={"move_to_top": True}).status_code == 200
    assert client.post(f"{API}/tags/{bills}/merge", json={"target_id": invoices}).status_code == 200
    with session_factory() as s:
        assert s.get(Tag, uuid.UUID(bills)) is None

    response = client.delete(f"{API}/tags/{category}")
    assert response.status_code == 409 and "sub-tags" in response.json()["detail"]
    client.delete(f"{API}/tags/{invoices}")
    assert client.delete(f"{API}/tags/{category}").status_code == 200


def test_entity_lifecycle(client, session_factory):
    office = new_entity(client, session_factory, "Urząd Skarbowy", "Institution")
    response = client.patch(
        f"{API}/entities/{office}",
        json={
            "name": "Urząd Skarbowy Example",
            "aliases": ["US", " ", "Tax office"],
            "attributes": {"tax id": "000-000-00-00"},
        },
    )
    assert response.status_code == 200
    detail = client.get(f"{API}/entities/{office}").json()
    assert detail["name"] == "Urząd Skarbowy Example"
    assert detail["aliases"] == ["US", "Tax office"]
    assert detail["attributes"] == {"tax id": "000-000-00-00"}

    sender = new_tag(client, "sender")
    assert (
        client.post(f"{API}/tags", json={"parent_id": sender, "entity_id": office}).status_code
        == 200
    )
    paths = {t["path"] for t in client.get(f"{API}/tags").json()}
    assert "sender / Urząd Skarbowy Example" in paths

    response = client.delete(f"{API}/entities/{office}")
    assert response.status_code == 409 and "used by 1 tag" in response.json()["detail"]


def test_saving_entity_details_does_not_touch_tag_history(client, session_factory):
    anna = new_entity(client, session_factory, "Anna Nowak")
    client.post(f"{API}/tags", json={"parent_id": new_tag(client, "sender"), "entity_id": anna})
    client.patch(f"{API}/entities/{anna}", json={"attributes": {"e-mail": "anna@example.com"}})
    with session_factory() as s:
        tag_updates = s.scalars(
            select(AuditLog).where(AuditLog.object_type == "tag", AuditLog.action == "UPDATE")
        ).all()
    assert tag_updates == []


def test_history_labels_link_into_the_app(client):
    category = new_tag(client, "Urząd <b>")
    entries = client.get(f"{API}/history").json()
    created = next(e for e in entries if e["object_id"] == category)
    assert (created["label"], created["link"]) == ("Urząd <b>", f"/tags?id={category}")
    assert created["new_value"]["name"] == "Urząd <b>"  # raw value; the interface escapes it


@pytest.mark.parametrize("url", [f"{API}/tags", f"{API}/entities"])
def test_mutations_require_csrf(config, session_factory, url):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    body = {"name": "x", "type_id": type_id(session_factory, "Person")}
    assert plain.post(url, json=body if "entities" in url else {"name": "x"}).status_code == 403


def test_blank_names_are_refused_not_crashing(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        headers={CSRF_HEADER: TOKEN},
        raise_server_exceptions=False,
    )
    person = type_id(session_factory, "Person")
    tag = new_tag(client, "a")
    entity = new_entity(client, session_factory, "Jan Kowalski")
    for method, url, body in [
        ("post", f"{API}/tags", {"name": "  "}),
        ("patch", f"{API}/tags/{tag}", {"name": " "}),
        ("post", f"{API}/entities", {"name": "  ", "type_id": person}),
        ("patch", f"{API}/entities/{entity}", {"name": " "}),
        ("post", f"{API}/entity-types", {"name": " "}),
    ]:
        response = getattr(client, method)(url, json=body)
        assert response.status_code in (409, 422), (url, response.status_code)
