"""Entity operations: people and organizations defined once (see docs/adr/0004)."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import Entity, EntityType, Tag
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


def create_entity(
    session: Session,
    type_: EntityType,
    name: str,
    *,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Entity:
    name = clean_name(name)
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
        entity.aliases = [clean_name(a) for a in aliases]
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
    record(session, Action.DELETE, entity, old=_snapshot(entity))
    session.delete(entity)
    session.flush()
