import re

import pytest
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.models import AuditLog, Entity, Tag
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def tag_id(session_factory, name: str) -> str:
    with session_factory() as s:
        return str(s.scalars(select(Tag.id).where(Tag.name == name)).one())


def type_id(session_factory, name: str) -> str:
    with session_factory() as s:
        return str(etype(s, name).id)


def entity_id(session_factory, name: str) -> str:
    with session_factory() as s:
        return str(s.scalars(select(Entity.id).where(Entity.name == name)).one())


def test_pages_render(client):
    for url in ("/tags", "/entities", "/history"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert 'href="/tags"' in response.text


def test_build_tree_through_ui(client, session_factory):
    r = client.post("/tags/create", data={"name": "category"})
    assert "Tag created." in r.text
    category = tag_id(session_factory, "category")
    client.post("/tags/create", data={"name": "taxes", "parent_id": category})
    taxes = tag_id(session_factory, "taxes")
    r = client.post("/tags/create", data={"name": "PIT", "parent_id": taxes})
    assert "category / taxes / PIT" in r.text  # new tag selected, full path shown

    r = client.get(f"/tags?id={taxes}")
    assert "<h2>category / taxes</h2>" in r.text
    assert "CREATE" in r.text  # history panel


def test_duplicate_name_shows_error_without_changes(client, session_factory):
    client.post("/tags/create", data={"name": "sender"})
    r = client.post("/tags/create", data={"name": "SENDER"})
    assert 'class="flash error"' in r.text and "already exists" in r.text
    with session_factory() as s:
        assert len(s.scalars(select(Tag)).all()) == 1


def test_rename_move_merge_delete(client, session_factory):
    client.post("/tags/create", data={"name": "category"})
    category = tag_id(session_factory, "category")
    client.post("/tags/create", data={"name": "bills", "parent_id": category})
    client.post("/tags/create", data={"name": "invoices", "parent_id": category})
    bills, invoices = tag_id(session_factory, "bills"), tag_id(session_factory, "invoices")

    r = client.post(f"/tags/{bills}/rename", data={"name": "Bills"})
    assert "Tag renamed." in r.text
    r = client.post(f"/tags/{bills}/move", data={"parent_id": ""})
    assert "Tag moved." in r.text
    r = client.post(f"/tags/{bills}/merge", data={"target_id": invoices})
    assert "Tags merged." in r.text
    with session_factory() as s:
        assert s.get(Tag, __import__("uuid").UUID(bills)) is None

    r = client.post(f"/tags/{category}/delete")
    assert "sub-tags" in r.text  # guarded
    client.post(f"/tags/{invoices}/delete")
    r = client.post(f"/tags/{category}/delete")
    assert "Tag deleted." in r.text


def test_entity_lifecycle(client, session_factory):
    r = client.post(
        "/entities/create",
        data={"name": "Urząd Skarbowy", "type_id": type_id(session_factory, "Institution")},
    )
    assert "Entity created." in r.text
    office = entity_id(session_factory, "Urząd Skarbowy")

    r = client.post(
        f"/entities/{office}/update",
        data={
            "name": "Urząd Skarbowy Example",
            "type_id": type_id(session_factory, "Institution"),
            "aliases": "US\n\nTax office\n",
            "attributes": "tax id: 000-000-00-00\naddress: ul. Przykładowa 1",
        },
    )
    assert "Entity saved." in r.text
    with session_factory() as s:
        entity = s.get(Entity, __import__("uuid").UUID(office))
        assert entity.aliases == ["US", "Tax office"]
        assert entity.attributes == {"tax id": "000-000-00-00", "address": "ul. Przykładowa 1"}

    client.post("/tags/create", data={"name": "sender"})
    sender = tag_id(session_factory, "sender")
    r = client.post(f"/entities/{office}/add-tag", data={"parent_id": sender})
    assert "sender / Urząd Skarbowy Example" in r.text

    r = client.post(f"/entities/{office}/delete")
    assert "used by 1 tag" in r.text


def test_saving_details_does_not_touch_tag_history(client, session_factory):
    client.post(
        "/entities/create",
        data={"name": "Anna Nowak", "type_id": type_id(session_factory, "Person")},
    )
    anna = entity_id(session_factory, "Anna Nowak")
    client.post("/tags/create", data={"name": "sender"})
    client.post(f"/entities/{anna}/add-tag", data={"parent_id": tag_id(session_factory, "sender")})
    client.post(
        f"/entities/{anna}/update",
        data={
            "name": "Anna Nowak",
            "type_id": type_id(session_factory, "Person"),
            "attributes": "e-mail: anna@example.com",
        },
    )
    with session_factory() as s:
        tag_updates = s.scalars(
            select(AuditLog).where(AuditLog.object_type == "tag", AuditLog.action == "UPDATE")
        ).all()
    assert tag_updates == []


def test_bad_attribute_line_keeps_user_input(client, session_factory):
    client.post(
        "/entities/create",
        data={"name": "Jan Kowalski", "type_id": type_id(session_factory, "Person")},
    )
    jan = entity_id(session_factory, "Jan Kowalski")
    r = client.post(
        f"/entities/{jan}/update",
        data={
            "name": "Jan Kowalski",
            "type_id": type_id(session_factory, "Person"),
            "attributes": "no colon here",
        },
    )
    assert "key: value" in r.text
    assert "no colon here</textarea>" in r.text


def test_user_input_is_escaped(client):
    r = client.post("/tags/create", data={"name": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_history_page_lists_changes_with_links(client, session_factory):
    client.post("/tags/create", data={"name": "category"})
    r = client.get("/history")
    assert re.search(r'<a href="/tags\?id=[0-9a-f-]+">category</a>', r.text)


def test_history_values_are_readable_and_escaped(client):
    client.post("/tags/create", data={"name": "Urząd <b>"})
    r = client.get("/history")
    assert "Urząd &lt;b&gt;" in r.text
    assert "\\u0105" not in r.text


@pytest.mark.parametrize("url", ["/tags/create", "/entities/create"])
def test_mutations_require_csrf(config, session_factory, url):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    assert (
        plain.post(
            url, data={"name": "x", "type_id": type_id(session_factory, "Person")}
        ).status_code
        == 403
    )
