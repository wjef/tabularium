import uuid

import pytest
from alembic import command
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tabularium import db
from tabularium.models import AuditLog, Entity, EntityType
from tabularium.services import DomainError
from tabularium.services.entities import create_entity, update_entity
from tabularium.services.entity_types import create_type, delete_type, list_types, rename_type
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


def test_starting_types_exist_and_are_ordered(session):
    assert [t.name for t in list_types(session)] == [
        "Person",
        "Organization",
        "Institution",
        "Other",
    ]


def test_create_rename_delete_with_guards(session):
    bank = create_type(session, "  Bank  ")
    assert bank.name == "Bank" and list_types(session)[-1] is bank
    with pytest.raises(DomainError, match="already exists"):
        create_type(session, "BANK")
    with pytest.raises(DomainError, match="already exists"):
        rename_type(session, bank, "person")

    rename_type(session, bank, "Bank / Insurer")
    entity = create_entity(session, bank, "Example Bank S.A.")
    with pytest.raises(DomainError, match="used by 1 entity"):
        delete_type(session, bank)

    update_entity(session, entity, type_=etype(session, "Organization"))
    delete_type(session, bank)
    session.commit()
    actions = session.scalars(
        select(AuditLog.action).where(AuditLog.object_type == "entity_type").order_by(AuditLog.id)
    ).all()
    assert actions == ["CREATE", "UPDATE", "DELETE"]
    entity_update = session.scalars(
        select(AuditLog).where(AuditLog.object_type == "entity", AuditLog.action == "UPDATE")
    ).one()
    assert entity_update.old_value["type"] == "Bank / Insurer"
    assert entity_update.new_value["type"] == "Organization"


def test_migration_converts_existing_entities(tmp_path):
    """Entities stored with the old fixed type keep their meaning after upgrading."""
    url = db.sqlite_url(tmp_path / "old.db")
    cfg = db.alembic_config(url)
    command.upgrade(cfg, "0002")
    engine = db.make_engine(url)
    ids = {}
    with engine.begin() as conn:
        for code in ("INSTITUTION", "PERSON", "SOMETHING_ODD"):
            ids[code] = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO entity (id, type, name, normalized_name, aliases, attributes,"
                    " created_at, updated_at) VALUES (:id, :type, :name, :name, '[]', '{}',"
                    " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                ),
                {"id": ids[code].hex, "type": code, "name": code.lower()},
            )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = db.make_engine(url)
    with db.make_session_factory(engine)() as s:
        types = {e.id: e.type.name for e in s.scalars(select(Entity))}
    engine.dispose()
    assert types == {
        ids["INSTITUTION"]: "Institution",
        ids["PERSON"]: "Person",
        ids["SOMETHING_ODD"]: "Other",
    }

    command.downgrade(cfg, "0002")  # and back again, values restored
    engine = db.make_engine(url)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT name, type FROM entity")).all())
    engine.dispose()
    assert rows == {"institution": "INSTITUTION", "person": "PERSON", "something_odd": "OTHER"}


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_types_are_managed_from_the_entities_page(client, session_factory):
    r = client.post("/entities/types/create", data={"name": "School"})
    assert "Entity type created." in r.text
    with session_factory() as s:
        school = s.scalars(select(EntityType).where(EntityType.name == "School")).one()

    r = client.post(
        "/entities/create", data={"name": "Szkoła Podstawowa nr 1", "type_id": str(school.id)}
    )
    assert "Entity created." in r.text
    assert "<td>School</td>" in r.text

    r = client.post(f"/entities/types/{school.id}/delete")
    assert "used by 1 entity" in r.text

    r = client.post(f"/entities/types/{school.id}/rename", data={"name": "School / University"})
    assert "Entity type renamed." in r.text
    assert "<td>School / University</td>" in r.text


def test_creating_entity_without_type_shows_error(client):
    r = client.post("/entities/create", data={"name": "Nobody"})
    assert "Choose an entity type." in r.text
