"""Entity operations: people and organizations defined once (see docs/adr/0004)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import Entity, EntityType, Tag
from tabularium.services import DomainError
from tabularium.services.tags import DuplicateTagError, check_name_free
from tabularium.text import clean_name, normalize


def _snapshot(entity: Entity) -> dict:
    return {
        "type": entity.type.value,
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
        type=EntityType(type_),
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
    if name is not None:
        name = clean_name(name)
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
        entity.type = EntityType(type_)
    if aliases is not None:
        entity.aliases = [clean_name(a) for a in aliases]
    if attributes is not None:
        entity.attributes = dict(attributes)
    new = _snapshot(entity)
    if new != old:
        record(session, Action.UPDATE, entity, old=old, new=new)
    session.flush()
    return entity
