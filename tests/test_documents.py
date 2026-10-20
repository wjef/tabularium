import hashlib
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tabularium.models import AuditLog, Document, DocumentRelation, RelationType
from tabularium.services import DomainError
from tabularium.services.documents import (
    add_tag,
    create_document,
    inbox_documents,
    remove_tag,
    set_document_date,
    set_retain_until,
    set_tagging_done,
    update_details,
)
from tabularium.services.tags import create_tag

YEARS = 5
INGESTED = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)


def make_doc(session, content: bytes = b"invoice", name: str = "invoice.pdf") -> Document:
    sha = hashlib.sha256(content).hexdigest()
    return create_document(
        session,
        sha256=sha,
        stored_path=f"{sha[:2]}/{sha}.pdf",
        original_filename=name,
        size_bytes=len(content),
        retention_years=YEARS,
        ingested_at=INGESTED,
    )


def test_new_document_gets_default_retention_and_utc_times(session):
    doc = make_doc(session)
    session.commit()
    session.expire_all()
    loaded = session.get(Document, doc.id)
    assert loaded.retain_until == date(2031, 12, 31)
    assert not loaded.retain_until_manual
    assert loaded.ingested_at == INGESTED
    assert loaded.ingested_at.tzinfo is UTC


def test_naive_datetimes_are_rejected(session):
    with pytest.raises(Exception, match="naive"):
        create_document(
            session,
            sha256="a" * 64,
            stored_path="aa/x.pdf",
            original_filename="x.pdf",
            size_bytes=1,
            retention_years=YEARS,
            ingested_at=datetime(2026, 1, 1),  # noqa: DTZ001 — deliberately naive
        )


def test_duplicate_hash_rejected(session):
    make_doc(session)
    with pytest.raises(DomainError, match="already archived"):
        make_doc(session, name="copy.pdf")


def test_document_date_recomputes_retention(session):
    doc = make_doc(session)
    set_document_date(session, doc, date(2024, 5, 1), retention_years=YEARS)
    assert doc.retain_until == date(2029, 12, 31)


def test_manual_retention_wins_until_reset(session):
    doc = make_doc(session)
    set_retain_until(session, doc, date(2040, 1, 1), retention_years=YEARS)
    set_document_date(session, doc, date(2024, 5, 1), retention_years=YEARS)
    assert doc.retain_until == date(2040, 1, 1) and doc.retain_until_manual
    set_retain_until(session, doc, None, retention_years=YEARS)
    assert doc.retain_until == date(2029, 12, 31) and not doc.retain_until_manual


def test_inbox_keeps_documents_until_tagging_is_done(session):
    first, second = make_doc(session, b"one"), make_doc(session, b"two")
    tag = create_tag(session, "category")
    session.commit()
    assert set(session.scalars(inbox_documents())) == {first, second}

    assert add_tag(session, first, tag) is True
    assert add_tag(session, first, tag) is False  # idempotent
    session.commit()
    assert set(session.scalars(inbox_documents())) == {first, second}  # still being tagged

    assert set_tagging_done(session, first, True) is True
    assert set_tagging_done(session, first, True) is False  # already done
    session.commit()
    assert session.scalars(inbox_documents()).all() == [second]
    assert first.tagging_done_at.tzinfo is not None  # UTC

    assert remove_tag(session, first, tag) is True  # removing tags does not reopen it
    session.commit()
    assert session.scalars(inbox_documents()).all() == [second]

    assert set_tagging_done(session, first, False) is True  # "Revise tagging"
    session.commit()
    assert len(session.scalars(inbox_documents()).all()) == 2


def test_tag_in_use_cannot_be_deleted(session):
    doc, tag = make_doc(session), create_tag(session, "category")
    add_tag(session, doc, tag)
    session.commit()
    session.delete(tag)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_relations_reject_self_links(session):
    first, second = make_doc(session, b"invoice"), make_doc(session, b"payment")
    session.add(
        DocumentRelation(
            from_document_id=second.id, to_document_id=first.id, type=RelationType.PAYMENT_FOR
        )
    )
    session.commit()
    session.add(
        DocumentRelation(from_document_id=first.id, to_document_id=first.id, type="RELATED")
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_document_changes_are_audited_in_same_transaction(session):
    doc, tag = make_doc(session), create_tag(session, "category")
    add_tag(session, doc, tag)
    update_details(session, doc, title="  Invoice 03/2026 ")
    session.commit()
    entries = session.scalars(
        select(AuditLog).where(AuditLog.object_id == str(doc.id)).order_by(AuditLog.id)
    ).all()
    assert [e.action for e in entries] == ["CREATE", "TAG_ADD", "UPDATE"]
    assert entries[1].new_value == {"tag_id": str(tag.id), "tag": "category"}
    assert entries[2].new_value["title"] == "Invoice 03/2026"


def test_rolled_back_change_leaves_no_audit_entry(session):
    doc = make_doc(session)
    session.commit()
    set_document_date(session, doc, date(2020, 1, 1), retention_years=YEARS)
    session.rollback()
    actions = session.scalars(select(AuditLog.action)).all()
    assert actions == ["CREATE"]
