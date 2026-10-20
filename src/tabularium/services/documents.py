"""Document operations: creation, dates, retention and tagging."""

import uuid
from datetime import date, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.db import utcnow
from tabularium.models import Document, DocumentTag, Tag
from tabularium.retention import default_retain_until
from tabularium.services import DomainError


def create_document(
    session: Session,
    *,
    sha256: str,
    stored_path: str,
    original_filename: str,
    size_bytes: int,
    retention_years: int,
    original_relpath: str | None = None,
    original_mtime: datetime | None = None,
    mime_type: str | None = None,
    ingested_at: datetime | None = None,
) -> Document:
    sha256 = sha256.lower()
    if session.scalar(select(Document.id).where(Document.sha256 == sha256)) is not None:
        raise DomainError(f"A document with hash {sha256} is already archived.")
    ingested_at = ingested_at or utcnow()
    document = Document(
        id=uuid.uuid4(),
        sha256=sha256,
        stored_path=stored_path,
        original_filename=original_filename,
        original_relpath=original_relpath,
        original_mtime=original_mtime,
        mime_type=mime_type,
        size_bytes=size_bytes,
        ingested_at=ingested_at,
        retain_until=default_retain_until(None, ingested_at, retention_years),
        retain_until_manual=False,
    )
    session.add(document)
    record(
        session,
        Action.CREATE,
        document,
        new={"sha256": sha256, "original_filename": original_filename},
    )
    session.flush()
    return document


def set_document_date(
    session: Session, document: Document, value: date | None, *, retention_years: int
) -> Document:
    """Set the document's own date; recomputes retention unless it was set manually."""
    old = {"document_date": document.document_date, "retain_until": document.retain_until}
    document.document_date = value
    if not document.retain_until_manual:
        document.retain_until = default_retain_until(value, document.ingested_at, retention_years)
    new = {"document_date": document.document_date, "retain_until": document.retain_until}
    if new != old:
        record(session, Action.UPDATE, document, old=old, new=new)
    session.flush()
    return document


def set_retain_until(
    session: Session, document: Document, value: date | None, *, retention_years: int
) -> Document:
    """Set retention manually; ``None`` returns to the automatic default."""
    old = {"retain_until": document.retain_until, "manual": document.retain_until_manual}
    if value is None:
        document.retain_until_manual = False
        document.retain_until = default_retain_until(
            document.document_date, document.ingested_at, retention_years
        )
    else:
        document.retain_until_manual = True
        document.retain_until = value
    new = {"retain_until": document.retain_until, "manual": document.retain_until_manual}
    if new != old:
        record(session, Action.UPDATE, document, old=old, new=new)
    session.flush()
    return document


def update_details(
    session: Session, document: Document, *, title: str | None = None, note: str | None = None
) -> Document:
    old = {"title": document.title, "note": document.note}
    if title is not None:
        document.title = title.strip() or None
    if note is not None:
        document.note = note.strip() or None
    new = {"title": document.title, "note": document.note}
    if new != old:
        record(session, Action.UPDATE, document, old=old, new=new)
    session.flush()
    return document


def add_tag(session: Session, document: Document, tag: Tag) -> bool:
    """Assign a tag. Returns False if it was already assigned."""
    if session.get(DocumentTag, (document.id, tag.id)) is not None:
        return False
    session.add(DocumentTag(document_id=document.id, tag_id=tag.id))
    record(session, Action.TAG_ADD, document, new={"tag_id": tag.id, "tag": tag.name})
    session.flush()
    return True


def remove_tag(session: Session, document: Document, tag: Tag) -> bool:
    """Unassign a tag. Returns False if it was not assigned."""
    link = session.get(DocumentTag, (document.id, tag.id))
    if link is None:
        return False
    session.delete(link)
    record(session, Action.TAG_REMOVE, document, old={"tag_id": tag.id, "tag": tag.name})
    session.flush()
    return True


def inbox_documents() -> Select[tuple[Document]]:
    """The "To be tagged" view: documents whose tagging is not marked done, oldest first.

    Tagging a document does not take it out of the view; the user marks it done when
    finished (see ``set_tagging_done``), so several documents can be worked on at once.
    """
    return (
        select(Document)
        .where(Document.tagging_done_at.is_(None))
        .order_by(Document.ingested_at, Document.id)
    )


def set_tagging_done(session: Session, document: Document, done: bool) -> bool:
    """Mark the initial tagging as finished (leaves "To be tagged") or as to be revised
    (back in "To be tagged"). Returns False when nothing changed."""
    if (document.tagging_done_at is not None) == done:
        return False
    document.tagging_done_at = utcnow() if done else None
    old, new = ("to do", "done") if done else ("done", "to do")
    record(session, Action.UPDATE, document, old={"tagging": old}, new={"tagging": new})
    session.flush()
    return True


def bulk_add_tag(session: Session, documents: list[Document], tag: Tag) -> int:
    """Assign ``tag`` to every document; returns how many did not have it yet."""
    return sum(add_tag(session, document, tag) for document in documents)
