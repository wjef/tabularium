"""Tag tree operations (see docs/adr/0004)."""

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import DocumentTag, Entity, Tag
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


def document_counts(session: Session) -> dict[uuid.UUID, int]:
    """Number of documents directly assigned to each tag (tags without documents are absent)."""
    rows = session.execute(
        select(DocumentTag.tag_id, func.count()).group_by(DocumentTag.tag_id)
    ).all()
    return {tag_id: count for tag_id, count in rows}


def _usage(session: Session, tag: Tag) -> int:
    query = select(func.count()).select_from(DocumentTag).where(DocumentTag.tag_id == tag.id)
    return session.scalar(query) or 0


def _forget(session: Session, tag: Tag) -> None:
    """Drop ``tag`` from already-loaded collections after deleting it."""
    if tag.parent_id is not None:
        parent = session.get(Tag, tag.parent_id)
        if parent is not None:
            session.expire(parent, ["children"])
    if tag.entity_id is not None:
        entity = session.get(Entity, tag.entity_id)
        if entity is not None:
            session.expire(entity, ["tags"])


def delete_tag(session: Session, tag: Tag) -> None:
    """Delete an unused leaf tag. Tags in use must be merged into another tag instead."""
    if session.scalar(select(Tag.id).where(Tag.parent_id == tag.id).limit(1)) is not None:
        raise DomainError("This tag has sub-tags; move or delete them first.")
    used = _usage(session, tag)
    if used:
        raise DomainError(
            f"This tag is used by {used} document(s); merge it into another tag instead."
        )
    record(session, Action.DELETE, tag, old=_snapshot(tag))
    _forget(session, tag)
    session.delete(tag)
    session.flush()


def merge_tag(session: Session, source: Tag, target: Tag) -> int:
    """Move everything from ``source`` to ``target`` and delete ``source``.

    Documents tagged with ``source`` get ``target`` instead; sub-tags of ``source`` move under
    ``target``. Returns the number of documents re-tagged. Nothing changes if a sub-tag name
    already exists under ``target``.
    """
    if source.id == target.id:
        raise DomainError("A tag cannot be merged into itself.")
    if target.id in descendant_ids(session, [source.id]):
        raise DomainError("A tag cannot be merged into one of its own sub-tags.")
    children = list(session.scalars(select(Tag).where(Tag.parent_id == source.id)))
    for child in children:
        try:
            check_name_free(session, target.id, child.name)
        except DuplicateTagError as exc:
            raise DomainError(
                f"Cannot merge: '{target.name}' already has a sub-tag named '{child.name}'."
            ) from exc

    links = list(session.scalars(select(DocumentTag).where(DocumentTag.tag_id == source.id)))
    already = set(
        session.scalars(
            select(DocumentTag.document_id).where(
                DocumentTag.tag_id == target.id,
                DocumentTag.document_id.in_([link.document_id for link in links]),
            )
        )
    )
    for link in links:
        document = link.document
        record(session, Action.TAG_REMOVE, document, old={"tag_id": source.id, "tag": source.name})
        if link.document_id not in already:
            session.add(DocumentTag(document_id=link.document_id, tag_id=target.id))
            record(session, Action.TAG_ADD, document, new={"tag_id": target.id, "tag": target.name})
        session.delete(link)
        session.expire(document, ["tag_links"])
    session.flush()

    for child in children:
        old = _snapshot(child)
        child.parent, child.parent_id = target, target.id
        record(session, Action.MOVE, child, old=old, new=_snapshot(child))
    session.flush()

    record(
        session,
        Action.MERGE,
        source,
        old=_snapshot(source),
        new={"into": target.id, "target": tag_path(target), "documents": len(links)},
    )
    session.expire(source, ["children"])
    _forget(session, source)
    session.delete(source)
    session.flush()
    return len(links)
