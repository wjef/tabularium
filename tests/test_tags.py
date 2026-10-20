import pytest
from conftest import etype
from sqlalchemy import select

from tabularium.models import AuditLog, Tag
from tabularium.services import DomainError
from tabularium.services.entities import create_entity, update_entity
from tabularium.services.tags import (
    DuplicateTagError,
    create_tag,
    descendant_ids,
    facet_of,
    move_tag,
    rename_tag,
    tag_path,
)


@pytest.fixture
def tree(session):
    """sender → (Jan Kowalski entity, Urząd Skarbowy entity); category → taxes → PIT."""
    jan = create_entity(session, etype(session, "Person"), "Jan Kowalski")
    office = create_entity(session, etype(session, "Institution"), "Urząd Skarbowy")
    sender = create_tag(session, "sender")
    category = create_tag(session, "category")
    taxes = create_tag(session, "taxes", parent=category)
    tags = {
        "sender": sender,
        "sender/jan": create_tag(session, parent=sender, entity=jan),
        "sender/office": create_tag(session, parent=sender, entity=office),
        "category": category,
        "taxes": taxes,
        "pit": create_tag(session, "PIT", parent=taxes),
    }
    session.commit()
    return tags | {"jan": jan, "office": office}


def test_entity_tag_takes_entity_name_and_path(tree):
    assert tree["sender/jan"].name == "Jan Kowalski"
    assert tag_path(tree["pit"]) == "category / taxes / PIT"
    assert facet_of(tree["pit"]) is tree["category"]


def test_duplicate_names_rejected_case_and_diacritic_insensitive(session, tree):
    with pytest.raises(DuplicateTagError):
        create_tag(session, "urzad skarbowy", parent=tree["sender"])
    with pytest.raises(DuplicateTagError):
        create_tag(session, "SENDER")  # duplicate facet (root)


def test_same_name_allowed_under_different_parents(session, tree):
    create_tag(session, "PIT", parent=tree["category"])
    session.commit()


def test_database_enforces_unique_root_names(session, tree):
    """The partial unique index guards roots even if service checks are bypassed."""
    from sqlalchemy.exc import IntegrityError

    session.add(Tag(name="Sender", normalized_name="sender"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_same_entity_under_two_facets(session, tree):
    recipient = create_tag(session, "recipient")
    tag = create_tag(session, parent=recipient, entity=tree["jan"])
    session.commit()
    assert {t.id for t in tree["jan"].tags} == {tree["sender/jan"].id, tag.id}


def test_descendants_include_self_and_all_levels(session, tree):
    ids = descendant_ids(session, [tree["category"].id])
    assert ids == {tree["category"].id, tree["taxes"].id, tree["pit"].id}


def test_move_prevents_cycles(session, tree):
    with pytest.raises(DomainError):
        move_tag(session, tree["category"], tree["pit"])
    with pytest.raises(DomainError):
        move_tag(session, tree["taxes"], tree["taxes"])


def test_move_to_root_and_back(session, tree):
    move_tag(session, tree["taxes"], None)
    assert tree["taxes"].parent_id is None
    move_tag(session, tree["taxes"], tree["category"])
    session.commit()
    assert tag_path(tree["pit"]) == "category / taxes / PIT"


def test_paths_follow_a_move_even_when_already_loaded(session, tree):
    assert tag_path(tree["pit"]) == "category / taxes / PIT"  # loads .parent
    move_tag(session, tree["taxes"], None)
    assert tag_path(tree["pit"]) == "taxes / PIT"
    assert facet_of(tree["pit"]) is tree["taxes"]
    session.commit()
    assert tag_path(tree["pit"]) == "taxes / PIT"


def test_entity_rename_reaches_tags_created_after_first_load(session, tree):
    update_entity(session, tree["jan"], name="Jan A")  # loads jan.tags
    recipient = create_tag(session, "recipient")
    late = create_tag(session, parent=recipient, entity=tree["jan"])
    update_entity(session, tree["jan"], name="Jan B")
    session.commit()
    assert late.name == "Jan B"
    assert tree["sender/jan"].name == "Jan B"
    assert late in tree["jan"].tags


def test_rename_plain_tag_but_not_entity_tag(session, tree):
    rename_tag(session, tree["taxes"], "Taxes")
    assert tree["taxes"].normalized_name == "taxes"
    with pytest.raises(DomainError, match="rename the entity"):
        rename_tag(session, tree["sender/jan"], "Someone")


def test_entity_rename_propagates_to_its_tags(session, tree):
    update_entity(session, tree["jan"], name="Jan Kowalski-Nowak")
    session.commit()
    assert tree["sender/jan"].name == "Jan Kowalski-Nowak"
    assert tree["sender/jan"].normalized_name == "jan kowalski-nowak"


def test_entity_rename_blocked_when_its_tag_would_clash(session, tree):
    create_tag(session, "Anna Nowak", parent=tree["sender"])  # a plain tag next to Jan's tag
    with pytest.raises(DomainError, match="Cannot rename"):
        update_entity(session, tree["jan"], name="anna nowak")


def test_entity_names_are_unique_whatever_the_type(session, tree):
    with pytest.raises(DomainError, match="already exists"):
        create_entity(session, etype(session, "Organization"), "URZAD  skarbowy")
    with pytest.raises(DomainError, match="already exists"):
        update_entity(session, tree["jan"], name="urząd skarbowy")
    update_entity(session, tree["jan"], name="Jan  KOWALSKI")  # its own name, other spelling
    assert tree["jan"].name == "Jan KOWALSKI"


def test_tag_operations_are_audited(session, tree):
    rename_tag(session, tree["taxes"], "Tax matters")
    move_tag(session, tree["pit"], tree["category"])
    session.commit()
    actions = session.scalars(
        select(AuditLog.action).where(AuditLog.object_type == "tag").order_by(AuditLog.id)
    ).all()
    assert actions.count("CREATE") == 6
    assert actions[-2:] == ["UPDATE", "MOVE"]
    last = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert last.old_value["parent_id"] == str(tree["taxes"].id)
    assert last.new_value["parent_id"] == str(tree["category"].id)
    assert last.actor
