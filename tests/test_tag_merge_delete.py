import hashlib

import pytest
from sqlalchemy import select

from tabularium.models import AuditLog, DocumentTag, EntityType, Tag
from tabularium.services import DomainError
from tabularium.services.documents import add_tag, create_document
from tabularium.services.entities import create_entity, delete_entity
from tabularium.services.tags import create_tag, delete_tag, document_counts, merge_tag, tag_path


def doc(session, content: bytes):
    sha = hashlib.sha256(content).hexdigest()
    return create_document(
        session,
        sha256=sha,
        stored_path=f"{sha[:2]}/{sha}.pdf",
        original_filename=f"{content.decode()}.pdf",
        size_bytes=len(content),
        retention_years=5,
    )


@pytest.fixture
def setup(session):
    category = create_tag(session, "category")
    invoices = create_tag(session, "invoices", parent=category)
    bills = create_tag(session, "bills", parent=category)
    electricity = create_tag(session, "electricity", parent=bills)
    d1, d2, d3 = doc(session, b"one"), doc(session, b"two"), doc(session, b"three")
    add_tag(session, d1, bills)
    add_tag(session, d2, bills)
    add_tag(session, d2, invoices)  # already has the target
    add_tag(session, d3, invoices)
    session.commit()
    return {
        "category": category,
        "invoices": invoices,
        "bills": bills,
        "electricity": electricity,
        "docs": (d1, d2, d3),
    }


def tags_of(session, document) -> set[str]:
    rows = session.scalars(
        select(Tag.name).join(DocumentTag).where(DocumentTag.document_id == document.id)
    )
    return set(rows)


def test_merge_moves_documents_and_children_and_removes_source(session, setup):
    d1, d2, d3 = setup["docs"]
    moved = merge_tag(session, setup["bills"], setup["invoices"])
    session.commit()

    assert moved == 2
    assert session.get(Tag, setup["bills"].id) is None
    assert tags_of(session, d1) == {"invoices"}
    assert tags_of(session, d2) == {"invoices"}  # no duplicate link
    assert tags_of(session, d3) == {"invoices"}
    assert tag_path(setup["electricity"]) == "category / invoices / electricity"
    assert document_counts(session)[setup["invoices"].id] == 3


def test_merge_is_audited_on_tag_and_documents(session, setup):
    d1 = setup["docs"][0]
    merge_tag(session, setup["bills"], setup["invoices"])
    session.commit()
    merge_entry = session.scalars(select(AuditLog).where(AuditLog.action == "MERGE")).one()
    assert merge_entry.object_id == str(setup["bills"].id)
    assert merge_entry.new_value["target"] == "category / invoices"
    assert merge_entry.new_value["documents"] == 2
    doc_actions = session.scalars(
        select(AuditLog.action)
        .where(AuditLog.object_id == str(d1.id), AuditLog.action.like("TAG_%"))
        .order_by(AuditLog.id)
    ).all()
    assert doc_actions == ["TAG_ADD", "TAG_REMOVE", "TAG_ADD"]


def test_merge_rejects_self_and_own_descendants(session, setup):
    with pytest.raises(DomainError):
        merge_tag(session, setup["bills"], setup["bills"])
    with pytest.raises(DomainError):
        merge_tag(session, setup["category"], setup["electricity"])


def test_merge_conflicting_child_names_changes_nothing(session, setup):
    create_tag(session, "electricity", parent=setup["invoices"])
    session.commit()
    with pytest.raises(DomainError, match="already has a sub-tag"):
        merge_tag(session, setup["bills"], setup["invoices"])
    session.rollback()
    assert session.get(Tag, setup["bills"].id) is not None
    assert tags_of(session, setup["docs"][0]) == {"bills"}


def test_delete_guards(session, setup):
    with pytest.raises(DomainError, match="sub-tags"):
        delete_tag(session, setup["category"])
    with pytest.raises(DomainError, match="used by 2 document"):
        delete_tag(session, setup["invoices"])


def test_delete_unused_leaf_is_audited(session, setup):
    tag_id = setup["electricity"].id
    delete_tag(session, setup["electricity"])
    session.commit()
    assert session.get(Tag, tag_id) is None
    assert setup["electricity"] not in setup["bills"].children
    entry = session.scalars(select(AuditLog).where(AuditLog.action == "DELETE")).one()
    assert entry.old_value["name"] == "electricity"


def test_entity_delete_guard(session):
    person = create_entity(session, EntityType.PERSON, "Anna Nowak")
    sender = create_tag(session, "sender")
    tag = create_tag(session, parent=sender, entity=person)
    session.commit()
    with pytest.raises(DomainError, match="used by 1 tag"):
        delete_entity(session, person)
    delete_tag(session, tag)
    delete_entity(session, person)
    session.commit()
    assert session.scalars(
        select(AuditLog.action).where(AuditLog.object_type == "entity")
    ).all() == [
        "CREATE",
        "DELETE",
    ]
