import hashlib
import uuid

import pytest
from alembic import command
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tabularium import db
from tabularium.models import AuditLog, EntityMembership
from tabularium.services import DomainError
from tabularium.services.documents import add_tag, create_document
from tabularium.services.entities import (
    add_membership,
    create_entity,
    delete_entity,
    groups_of,
    members_of,
    related_entities,
    remove_membership,
)
from tabularium.services.search import SearchQuery, search
from tabularium.services.tags import create_tag
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def family(session):
    """Jan and Anna are in the Kowalski Family, which is in the Extended Family.
    Ewa is only in Example Company."""
    person, org = etype(session, "Person"), etype(session, "Organization")
    e = {
        "jan": create_entity(session, person, "Jan Kowalski"),
        "anna": create_entity(session, person, "Anna Kowalska"),
        "ewa": create_entity(session, person, "Ewa Nowak"),
        "family": create_entity(session, etype(session, "Other"), "Kowalski Family"),
        "extended": create_entity(session, etype(session, "Other"), "Extended Family"),
        "company": create_entity(session, org, "Example Company"),
    }
    add_membership(session, e["jan"], e["family"])
    add_membership(session, e["anna"], e["family"])
    add_membership(session, e["family"], e["extended"])
    add_membership(session, e["ewa"], e["company"])
    session.commit()
    return e


def ids(*entities):
    return {x.id for x in entities}


def test_groups_and_members_are_followed_through_all_levels(session, family):
    f = family
    assert groups_of(session, {f["jan"].id}) == ids(f["family"], f["extended"])
    assert members_of(session, {f["extended"].id}) == ids(f["family"], f["jan"], f["anna"])
    assert related_entities(session, {f["family"].id}) == ids(
        f["family"], f["extended"], f["jan"], f["anna"]
    )
    # siblings are not "related": Anna is not reached from Jan
    assert f["anna"].id not in related_entities(session, {f["jan"].id})


def test_loops_and_self_membership_are_refused(session, family):
    f = family
    with pytest.raises(DomainError, match="itself"):
        add_membership(session, f["jan"], f["jan"])
    with pytest.raises(DomainError, match="loop"):
        add_membership(session, f["extended"], f["jan"])  # Jan is (indirectly) in Extended
    assert add_membership(session, f["jan"], f["family"]) is False  # already a member


def test_membership_changes_are_audited(session, family):
    f = family
    remove_membership(session, f["jan"], f["family"])
    session.commit()
    actions = session.scalars(
        select(AuditLog.action)
        .where(AuditLog.object_id == str(f["jan"].id), AuditLog.action.like("MEMBER_%"))
        .order_by(AuditLog.id)
    ).all()
    assert actions == ["MEMBER_ADD", "MEMBER_REMOVE"]


def test_deleting_an_entity_removes_its_memberships(session, family):
    f = family
    delete_entity(session, f["family"])
    session.commit()
    assert (
        session.scalars(
            select(EntityMembership).where(
                (EntityMembership.group_id == f["family"].id)
                | (EntityMembership.member_id == f["family"].id)
            )
        ).all()
        == []
    )
    assert groups_of(session, {f["jan"].id}) == set()


def _doc(session, name, *tags):
    sha = hashlib.sha256(name.encode()).hexdigest()
    d = create_document(
        session,
        sha256=sha,
        stored_path=f"{sha[:2]}/{sha}.pdf",
        original_filename=name,
        size_bytes=1,
        retention_years=5,
    )
    for t in tags:
        add_tag(session, d, t)
    return d


def test_search_with_and_without_related_entities(session, family):
    f = family
    recipient = create_tag(session, "recipient")
    to = {k: create_tag(session, parent=recipient, entity=f[k]) for k in f}
    _doc(session, "for-jan.pdf", to["jan"])
    _doc(session, "for-family.pdf", to["family"])
    _doc(session, "for-extended.pdf", to["extended"])
    _doc(session, "for-anna.pdf", to["anna"])
    _doc(session, "for-ewa.pdf", to["ewa"])
    session.commit()

    def names(entity, related):
        q = SearchQuery(entity_ids=[f[entity].id], include_related=related)
        return {d.original_filename for d in search(session, q)[0]}

    # exact
    assert names("jan", False) == {"for-jan.pdf"}
    assert names("family", False) == {"for-family.pdf"}
    # Jan + his groups (family, extended) — not his sister's documents
    assert names("jan", True) == {"for-jan.pdf", "for-family.pdf", "for-extended.pdf"}
    # family + its members + the group it belongs to
    assert names("family", True) == {
        "for-jan.pdf",
        "for-anna.pdf",
        "for-family.pdf",
        "for-extended.pdf",
    }
    assert names("ewa", True) == {"for-ewa.pdf"}


def test_migration_0004_on_existing_data(tmp_path):
    url = db.sqlite_url(tmp_path / "x.db")
    cfg = db.alembic_config(url)
    command.upgrade(cfg, "0003")
    engine = db.make_engine(url)
    with engine.begin() as conn:
        type_id = conn.execute(text("SELECT id FROM entity_type LIMIT 1")).scalar()
        eid = uuid.uuid4().hex
        conn.execute(
            text(
                "INSERT INTO entity (id, type_id, name, normalized_name, aliases, attributes,"
                " created_at, updated_at) VALUES (:id, :t, 'Anna Nowak', 'anna nowak', '[]',"
                " '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"id": eid, "t": type_id},
        )
        conn.execute(
            text(
                "INSERT INTO tag (id, parent_id, name, normalized_name, entity_id, sort_order,"
                " created_at) VALUES (:id, NULL, 'Anna Nowak', 'anna nowak', :e, 0,"
                " '2026-01-01 00:00:00')"
            ),
            {"id": uuid.uuid4().hex, "e": eid},
        )
    engine.dispose()
    db.upgrade(url)
    command.downgrade(cfg, "0003")
    db.upgrade(url)


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_membership_through_the_entities_page(client, session_factory):
    with session_factory() as s, s.begin():
        jan = create_entity(s, etype(s, "Person"), "Jan Kowalski")
        family = create_entity(s, etype(s, "Other"), "Kowalski Family")
        jan_id, family_id = str(jan.id), str(family.id)

    r = client.post(f"/entities/{jan_id}/groups/add", data={"other_id": family_id})
    assert "Added to the group." in r.text
    r = client.get(f"/entities?id={family_id}")
    assert f'<a href="/entities?id={jan_id}">Jan Kowalski</a>' in r.text  # listed as member
    # the family can no longer be offered as a member of Jan (loop)
    jan_page = client.get(f"/entities?id={jan_id}").text
    assert f'<option value="{family_id}">' not in jan_page
    assert f"/entities/{jan_id}/members/add" not in jan_page  # nothing left to offer

    r = client.post(f"/entities/{family_id}/members/add", data={"other_id": family_id})
    assert "itself" in r.text
    r = client.post(f"/entities/{family_id}/members/{jan_id}/remove")
    assert "Member removed." in r.text
    with session_factory() as s:
        assert s.scalars(select(EntityMembership)).all() == []


def test_search_page_related_checkbox(client, session_factory):
    with session_factory() as s, s.begin():
        jan = create_entity(s, etype(s, "Person"), "Jan Kowalski")
        family = create_entity(s, etype(s, "Other"), "Kowalski Family")
        add_membership(s, jan, family)
        recipient = create_tag(s, "recipient")
        _doc(s, "for-family.pdf", create_tag(s, parent=recipient, entity=family))
        jan_id = str(jan.id)
    exact = client.get("/search", params={"entity": jan_id})
    assert "0 document(s) found." in exact.text
    related = client.get("/search", params={"entity": jan_id, "related": "1"})
    assert "1 document(s) found." in related.text
    assert 'name="related" value="1" checked' in related.text
    assert "related=1" in related.text  # kept in result links
