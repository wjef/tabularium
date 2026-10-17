"""User-defined entity types (Person, Company, Bank, School…)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, record
from tabularium.models import Entity, EntityType
from tabularium.services import DomainError
from tabularium.text import clean_name, normalize


def _snapshot(entity_type: EntityType) -> dict:
    return {"name": entity_type.name, "sort_order": entity_type.sort_order}


def _check_name_free(session: Session, name: str, exclude: uuid.UUID | None = None) -> None:
    query = select(EntityType.id).where(EntityType.normalized_name == normalize(name))
    if exclude is not None:
        query = query.where(EntityType.id != exclude)
    if session.scalar(query.limit(1)) is not None:
        raise DomainError(f"An entity type named '{name}' already exists.")


def list_types(session: Session) -> list[EntityType]:
    return list(
        session.scalars(select(EntityType).order_by(EntityType.sort_order, EntityType.name))
    )


def create_type(session: Session, name: str) -> EntityType:
    name = clean_name(name)
    _check_name_free(session, name)
    last = session.scalar(select(func.max(EntityType.sort_order))) or 0
    entity_type = EntityType(
        id=uuid.uuid4(), name=name, normalized_name=normalize(name), sort_order=last + 10
    )
    session.add(entity_type)
    record(session, Action.CREATE, entity_type, new=_snapshot(entity_type))
    session.flush()
    return entity_type


def rename_type(session: Session, entity_type: EntityType, name: str) -> EntityType:
    name = clean_name(name)
    if name == entity_type.name:
        return entity_type
    _check_name_free(session, name, exclude=entity_type.id)
    old = _snapshot(entity_type)
    entity_type.name, entity_type.normalized_name = name, normalize(name)
    record(session, Action.UPDATE, entity_type, old=old, new=_snapshot(entity_type))
    session.flush()
    return entity_type


def delete_type(session: Session, entity_type: EntityType) -> None:
    """Delete a type no entity uses."""
    used = session.scalar(
        select(func.count()).select_from(Entity).where(Entity.type_id == entity_type.id)
    )
    if used:
        raise DomainError(
            f"This type is used by {used} entit{'y' if used == 1 else 'ies'}; "
            "change their type first."
        )
    record(session, Action.DELETE, entity_type, old=_snapshot(entity_type))
    session.delete(entity_type)
    session.flush()
