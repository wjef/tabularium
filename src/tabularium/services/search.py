"""Document search (see docs/requirements.md §8.1).

- Selecting a tag includes all tags below it; also ticking a tag below it narrows to that one.
- Selected tags are grouped by facet (root tag): OR within a facet, AND across facets.
- Selected entities form one more group: documents with any tag pointing to one of them
  ("anything involving X or Y"). With ``include_related`` the chosen entities are widened
  to every group they belong to and every member they have (all levels).
- Text matches title, original file name, note, the hash (archive file name) and the
  document's extracted text (full-text index), ignoring case and diacritics.
- The date range applies to the document date; documents without one are then excluded.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Select, String, exists, func, or_, select
from sqlalchemy.orm import Session

from tabularium.models import Document, DocumentTag, Tag
from tabularium.services.entities import related_entities
from tabularium.services.tags import ancestors, descendant_ids, facet_of
from tabularium.text import normalize
from tabularium.textindex import matching_ids


@dataclass
class SearchQuery:
    tag_ids: list[uuid.UUID] = field(default_factory=list)
    entity_ids: list[uuid.UUID] = field(default_factory=list)
    text: str = ""
    date_from: date | None = None
    date_to: date | None = None
    include_related: bool = False

    @property
    def is_empty(self) -> bool:
        return not (
            self.tag_ids or self.entity_ids or self.text.strip() or self.date_from or self.date_to
        )


def _has_any_tag(tag_ids) -> exists:
    return exists().where(DocumentTag.document_id == Document.id, DocumentTag.tag_id.in_(tag_ids))


def facet_groups(session: Session, tag_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Facet id → selected tags in that facet, each expanded with everything below it.

    A selected tag that is an ancestor of another selected tag is dropped: ticking "sender"
    and "sender / Jan Kowalski" means "sent by Jan", not "sent by anyone".
    """
    tags = [t for t in (session.get(Tag, i) for i in dict.fromkeys(tag_ids)) if t is not None]
    refined = {a.id for t in tags for a in ancestors(t)[:-1]}
    groups: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for tag in tags:
        if tag.id not in refined:
            groups[facet_of(tag).id] |= descendant_ids(session, [tag.id])
    return groups


def build_query(session: Session, query: SearchQuery) -> Select[tuple[Document]]:
    stmt = select(Document)
    for tag_ids in facet_groups(session, query.tag_ids).values():
        stmt = stmt.where(_has_any_tag(tag_ids))
    if query.entity_ids:
        entity_ids = set(query.entity_ids)
        if query.include_related:
            entity_ids = related_entities(session, entity_ids)
        entity_tags = select(Tag.id).where(Tag.entity_id.in_(entity_ids))
        stmt = stmt.where(_has_any_tag(entity_tags))
    for word in normalize(query.text).split():
        stmt = stmt.where(
            or_(
                Document.id.in_(matching_ids(word)),
                Document.sha256.contains(word, autoescape=True),
                func.tnorm(Document.title, type_=String).contains(word, autoescape=True),
                func.tnorm(Document.original_filename, type_=String).contains(
                    word, autoescape=True
                ),
                func.tnorm(Document.note, type_=String).contains(word, autoescape=True),
            )
        )
    if query.date_from:
        stmt = stmt.where(Document.document_date >= query.date_from)
    if query.date_to:
        stmt = stmt.where(Document.document_date <= query.date_to)
    return stmt.order_by(
        Document.document_date.is_(None),
        Document.document_date.desc(),
        Document.ingested_at.desc(),
    )


def search(session: Session, query: SearchQuery, *, limit: int = 500) -> tuple[list[Document], int]:
    """Matching documents (newest document date first) and the total number of matches."""
    stmt = build_query(session, query)
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    return list(session.scalars(stmt.limit(limit))), total
