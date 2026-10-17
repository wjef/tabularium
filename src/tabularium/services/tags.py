"""Tag tree operations (see docs/adr/0004)."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import Entity, Tag
from tabularium.services import DomainError
from tabularium.text import clean_name, normalize


class DuplicateTagError(DomainError):
    pass


def check_name_free(session: Session, parent_id: uuid.UUID | None, name: str, exclude=None) -> None:
    query = select(Tag.id).where(Tag.normalized_name == normalize(name))
    query = query.where(
        Tag.parent_id.is_(None) if parent_id is None else Tag.parent_id == parent_id
    )
    if exclude is not None:
        query = query.where(Tag.id != exclude)
    if session.scalar(query.limit(1)) is not None:
        raise DuplicateTagError(f"A tag named '{name}' already exists here.")


def _snapshot(tag: Tag) -> dict:
    return {"name": tag.name, "parent_id": tag.parent_id, "entity_id": tag.entity_id}


def create_tag(
    session: Session,
    name: str | None = None,
    *,
    parent: Tag | None = None,
    entity: Entity | None = None,
    sort_order: int = 0,
    color: str | None = None,
) -> Tag:
    """Create a tag. For an entity tag the name defaults to (and follows) the entity name."""
    if name is None:
        if entity is None:
            raise DomainError("A tag needs a name or an entity.")
        name = entity.name
    elif entity is not None and normalize(name) != entity.normalized_name:
        raise DomainError("An entity tag is named after its entity.")
    name = clean_name(name)
    parent_id = parent.id if parent else None
    check_name_free(session, parent_id, name)

    # Relationships and FK columns are set together: relationships keep already-loaded
    # objects (parent.children, entity.tags) in sync, FKs make the audit snapshot exact.
    tag = Tag(
        id=uuid.uuid4(),
        name=name,
        normalized_name=normalize(name),
        parent=parent,
        parent_id=parent_id,
        entity=entity,
        entity_id=entity.id if entity else None,
        sort_order=sort_order,
        color=color,
    )
    session.add(tag)
    record(session, Action.CREATE, tag, new=_snapshot(tag))
    session.flush()
    return tag


def rename_tag(session: Session, tag: Tag, new_name: str) -> Tag:
    if tag.entity_id is not None:
        raise DomainError("This tag shows an entity; rename the entity instead.")
    new_name = clean_name(new_name)
    if new_name == tag.name:
        return tag
    check_name_free(session, tag.parent_id, new_name, exclude=tag.id)
    old = _snapshot(tag)
    tag.name, tag.normalized_name = new_name, normalize(new_name)
    record(session, Action.UPDATE, tag, old=old, new=_snapshot(tag))
    session.flush()
    return tag


def move_tag(session: Session, tag: Tag, new_parent: Tag | None) -> Tag:
    """Re-parent a tag (``None`` makes it a facet). Document assignments are unaffected."""
    new_parent_id = new_parent.id if new_parent else None
    if new_parent_id == tag.parent_id:
        return tag
    if new_parent_id is not None and new_parent_id in descendant_ids(session, [tag.id]):
        raise DomainError("A tag cannot be moved under itself or one of its descendants.")
    check_name_free(session, new_parent_id, tag.name, exclude=tag.id)
    old = _snapshot(tag)
    tag.parent, tag.parent_id = new_parent, new_parent_id
    record(session, Action.MOVE, tag, old=old, new=_snapshot(tag))
    session.flush()
    return tag


def descendant_ids(session: Session, tag_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The given tags plus everything below them (recursive CTE)."""
    tag_ids = list(tag_ids)
    if not tag_ids:
        return set()
    tree = select(Tag.id).where(Tag.id.in_(tag_ids)).cte("tree", recursive=True)
    tree = tree.union(select(Tag.id).where(Tag.parent_id == tree.c.id))
    return set(session.scalars(select(tree.c.id)))


def ancestors(tag: Tag) -> list[Tag]:
    """From the facet (root) down to ``tag`` itself."""
    chain = [tag]
    while chain[0].parent is not None:
        chain.insert(0, chain[0].parent)
    return chain


def facet_of(tag: Tag) -> Tag:
    return ancestors(tag)[0]


def tag_path(tag: Tag, separator: str = " / ") -> str:
    return separator.join(t.name for t in ancestors(tag))
