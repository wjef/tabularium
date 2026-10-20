"""Entity operations: people and organizations defined once (see docs/adr/0004)."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import Entity, EntityMembership, EntityType, Tag
from tabularium.services import DomainError
from tabularium.services.tags import DuplicateTagError, check_name_free
from tabularium.text import clean_name, normalize


def _snapshot(entity: Entity) -> dict:
    return {
        "type": entity.type.name,
        "name": entity.name,
        "aliases": list(entity.aliases),
        "attributes": dict(entity.attributes),
    }


def check_entity_name_free(session: Session, name: str, exclude: uuid.UUID | None = None) -> None:
    """Entity names are unique (ignoring case, diacritics and spacing), whatever their type."""
    query = select(Entity).where(Entity.normalized_name == normalize(name))
    if exclude is not None:
        query = query.where(Entity.id != exclude)
    existing = session.scalars(query).first()
    if existing is not None:
        raise DomainError(
            f"An entity named '{existing.name}' already exists ({existing.type.name}). "
            "To tell two apart, add something to the name, e.g. a city or a year."
        )


def create_entity(
    session: Session,
    type_: EntityType,
    name: str,
    *,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Entity:
    name = clean_name(name)
    check_entity_name_free(session, name)
    entity = Entity(
        id=uuid.uuid4(),
        type=type_,
        type_id=type_.id,
        name=name,
        normalized_name=normalize(name),
        aliases=[clean_name(a) for a in aliases or []],
        attributes=dict(attributes or {}),
    )
    session.add(entity)
    record(session, Action.CREATE, entity, new=_snapshot(entity))
    session.flush()
    return entity


def update_entity(
    session: Session,
    entity: Entity,
    *,
    name: str | None = None,
    type_: EntityType | None = None,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Entity:
    """Update details. A new name is propagated to every tag pointing to this entity."""
    old = _snapshot(entity)
    name = clean_name(name) if name is not None else None
    # Any visible change counts as a rename (also case or diacritics, e.g. "Lodz" → "Łódź").
    if name is not None and name != entity.name:
        check_entity_name_free(session, name, exclude=entity.id)
        # Query instead of entity.tags: a collection loaded earlier may miss newer tags.
        tags = session.scalars(select(Tag).where(Tag.entity_id == entity.id)).all()
        for tag in tags:
            try:
                check_name_free(session, tag.parent_id, name, exclude=tag.id)
            except DuplicateTagError as exc:
                raise DomainError(
                    f"Cannot rename: a tag named '{name}' already exists next to '{tag.name}'."
                ) from exc
        entity.name, entity.normalized_name = name, normalize(name)
        for tag in tags:
            tag_old = {"name": tag.name}
            tag.name, tag.normalized_name = name, entity.normalized_name
            record(session, Action.UPDATE, tag, old=tag_old, new={"name": name})
    if type_ is not None:
        entity.type, entity.type_id = type_, type_.id
    if aliases is not None:
        entity.aliases = [clean_name(a) for a in aliases if a.strip()]  # blank lines dropped
    if attributes is not None:
        entity.attributes = dict(attributes)
    new = _snapshot(entity)
    if new != old:
        record(session, Action.UPDATE, entity, old=old, new=new)
    session.flush()
    return entity


def delete_entity(session: Session, entity: Entity) -> None:
    """Delete an entity that no tag points to."""
    used = session.scalar(select(func.count()).select_from(Tag).where(Tag.entity_id == entity.id))
    if used:
        raise DomainError(
            f"This entity is used by {used} tag(s); delete or merge those tags first."
        )
    links = session.scalars(
        select(EntityMembership).where(
            or_(EntityMembership.member_id == entity.id, EntityMembership.group_id == entity.id)
        )
    ).all()
    for link in links:
        _unlink(session, link)
    record(session, Action.DELETE, entity, old=_snapshot(entity))
    session.delete(entity)
    session.flush()


# --- membership (person → family, family → extended family) ----------------------------


def _closure(session: Session, start: set[uuid.UUID], *, upward: bool) -> set[uuid.UUID]:
    """All entities reachable from ``start`` via memberships (excluding ``start``).

    Upward: groups they belong to (and their groups…). Downward: members (and their members…).
    """
    if not start:
        return set()
    near, far = (
        (EntityMembership.member_id, EntityMembership.group_id)
        if upward
        else (EntityMembership.group_id, EntityMembership.member_id)
    )
    walk = select(far.label("id")).where(near.in_(start)).cte("walk", recursive=True)
    walk = walk.union(select(far).where(near == walk.c.id))
    return set(session.scalars(select(walk.c.id))) - start


def groups_of(session: Session, entity_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    return _closure(session, set(entity_ids), upward=True)


def members_of(session: Session, entity_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    return _closure(session, set(entity_ids), upward=False)


def related_entities(session: Session, entity_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """The entities themselves, every group they belong to and every member they have."""
    ids = set(entity_ids)
    return ids | groups_of(session, ids) | members_of(session, ids)


def add_membership(session: Session, member: Entity, group: Entity) -> bool:
    """Make ``member`` part of ``group``. Returns False if it already was."""
    if member.id == group.id:
        raise DomainError("An entity cannot be a member of itself.")
    if group.id in members_of(session, {member.id}):
        raise DomainError(
            f"'{group.name}' is already (indirectly) a member of '{member.name}'; "
            "that would make a loop."
        )
    if session.get(EntityMembership, (member.id, group.id)) is not None:
        return False
    session.add(EntityMembership(member_id=member.id, group_id=group.id))
    record(session, Action.MEMBER_ADD, member, new={"group_id": group.id, "group": group.name})
    session.flush()
    return True


def _unlink(session: Session, link: EntityMembership) -> None:
    member, group = session.get(Entity, link.member_id), session.get(Entity, link.group_id)
    record(session, Action.MEMBER_REMOVE, member, old={"group_id": group.id, "group": group.name})
    session.delete(link)


def remove_membership(session: Session, member: Entity, group: Entity) -> bool:
    link = session.get(EntityMembership, (member.id, group.id))
    if link is None:
        return False
    _unlink(session, link)
    session.flush()
    return True
