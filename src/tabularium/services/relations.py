"""Links between documents (invoice ↔ payment, correction, signed version…).

A relation is stored once, directed (``from`` <type> ``to``); each document shows it in its
own words ("payment for" on the transfer, "paid by" on the invoice).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.i18n import N_
from tabularium.models import Document, DocumentRelation, RelationType
from tabularium.services import DomainError

# (type, direction) → phrase from the point of view of the document being looked at.
# "out": this document is ``from``; "in": this document is ``to``.
PHRASES: dict[tuple[RelationType, str], str] = {
    (RelationType.PAYMENT_FOR, "out"): N_("is the payment for"),
    (RelationType.PAYMENT_FOR, "in"): N_("is paid by"),
    (RelationType.CORRECTION_OF, "out"): N_("is a correction of"),
    (RelationType.CORRECTION_OF, "in"): N_("is corrected by"),
    (RelationType.SIGNED_VERSION_OF, "out"): N_("is the signed version of"),
    (RelationType.SIGNED_VERSION_OF, "in"): N_("has the signed version"),
    (RelationType.ATTACHMENT_OF, "out"): N_("is an attachment of"),
    (RelationType.ATTACHMENT_OF, "in"): N_("has the attachment"),
    (RelationType.REPLACES, "out"): N_("replaces"),
    (RelationType.REPLACES, "in"): N_("is replaced by"),
    (RelationType.RELATED, "out"): N_("is related to"),
    (RelationType.RELATED, "in"): N_("is related to"),
}
SYMMETRIC = frozenset({RelationType.RELATED})


def choices() -> list[tuple[str, str]]:
    """Options for "this document …": value ``TYPE:direction`` and its phrase."""
    result = []
    for (kind, direction), phrase in PHRASES.items():
        if kind in SYMMETRIC and direction == "in":
            continue
        result.append((f"{kind.value}:{direction}", phrase))
    return result


def parse_choice(value: str) -> tuple[RelationType, str]:
    kind, _, direction = value.partition(":")
    try:
        parsed = RelationType(kind)
    except ValueError as exc:
        raise DomainError("Choose the kind of link.") from exc
    if direction not in ("out", "in"):
        raise DomainError("Choose the kind of link.")
    return parsed, direction


@dataclass
class Link:
    relation: DocumentRelation
    other: Document
    phrase: str


def links_of(session: Session, document: Document) -> list[Link]:
    rows = session.scalars(
        select(DocumentRelation)
        .where(
            or_(
                DocumentRelation.from_document_id == document.id,
                DocumentRelation.to_document_id == document.id,
            )
        )
        .order_by(DocumentRelation.created_at)
    ).all()
    links = []
    for rel in rows:
        outgoing = rel.from_document_id == document.id
        other = session.get(Document, rel.to_document_id if outgoing else rel.from_document_id)
        links.append(Link(rel, other, PHRASES[(rel.type, "out" if outgoing else "in")]))
    return links


def _snapshot(rel: DocumentRelation, source: Document, target: Document) -> dict:
    return {
        "relation_id": rel.id,
        "type": rel.type.value,
        "from": source.display_name,
        "from_id": source.id,
        "to": target.display_name,
        "to_id": target.id,
    }


def add_link(
    session: Session,
    document: Document,
    other: Document,
    kind: RelationType,
    direction: str,
    note: str | None = None,
) -> DocumentRelation:
    """Link ``document`` and ``other``; ``direction`` "out" means document <kind> other."""
    if document.id == other.id:
        raise DomainError("A document cannot be linked to itself.")
    source, target = (document, other) if direction == "out" else (other, document)
    pairs = [
        and_(
            DocumentRelation.from_document_id == source.id,
            DocumentRelation.to_document_id == target.id,
        )
    ]
    if kind in SYMMETRIC:
        pairs.append(
            and_(
                DocumentRelation.from_document_id == target.id,
                DocumentRelation.to_document_id == source.id,
            )
        )
    exists = session.scalar(
        select(DocumentRelation.id).where(DocumentRelation.type == kind, or_(*pairs)).limit(1)
    )
    if exists is not None:
        raise DomainError("These documents are already linked this way.")
    rel = DocumentRelation(
        id=uuid.uuid4(),
        from_document_id=source.id,
        to_document_id=target.id,
        type=kind,
        note=(note or "").strip() or None,
    )
    session.add(rel)
    session.flush()
    data = _snapshot(rel, source, target)
    record(session, Action.RELATION_ADD, source, new=data)
    record(session, Action.RELATION_ADD, target, new=data)
    session.flush()
    return rel


def remove_link(session: Session, relation_id: uuid.UUID) -> None:
    rel = session.get(DocumentRelation, relation_id)
    if rel is None:
        raise DomainError("This link no longer exists.")
    source = session.get(Document, rel.from_document_id)
    target = session.get(Document, rel.to_document_id)
    data = _snapshot(rel, source, target)
    record(session, Action.RELATION_REMOVE, source, old=data)
    record(session, Action.RELATION_REMOVE, target, old=data)
    session.delete(rel)
    session.flush()
