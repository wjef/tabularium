"""Entity management: people and organizations with their details (see docs/adr/0004)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.i18n import N_
from tabularium.models import AuditLog, Entity, EntityMembership, EntityType, Tag
from tabularium.services import DomainError
from tabularium.services.entities import (
    add_membership,
    create_entity,
    delete_entity,
    groups_of,
    members_of,
    remove_membership,
    update_entity,
)
from tabularium.services.entity_types import (
    create_type,
    delete_type,
    list_types,
    rename_type,
)
from tabularium.services.tags import create_tag
from tabularium.web.deps import get_session, parse_uuid, render
from tabularium.web.routes_tags import build_tree

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_attributes(text: str) -> dict[str, Any]:
    """``key: value`` per line → dict. Keys must be unique."""
    result: dict[str, Any] = {}
    for line in parse_lines(text):
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise DomainError(f"Details must be written as 'key: value' (line: {line!r}).")
        if key in result:
            raise DomainError(f"Detail '{key}' appears twice.")
        result[key] = value
    return result


def format_attributes(attributes: dict[str, Any]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in attributes.items())


def workspace_context(
    session: Session,
    selected_id: uuid.UUID | None,
    *,
    message: str | None = None,
    error: str | None = None,
    form: dict | None = None,
) -> dict:
    entities = session.scalars(select(Entity).order_by(Entity.normalized_name)).all()
    selected = session.get(Entity, selected_id) if selected_id else None
    tag_nodes, history = [], []
    membership: dict = {}
    _, index = build_tree(session)
    if selected is not None:
        membership = membership_context(session, selected, entities)
        tag_ids = session.scalars(select(Tag.id).where(Tag.entity_id == selected.id)).all()
        tag_nodes = sorted((index[t] for t in tag_ids), key=lambda n: n.path.casefold())
        history = session.scalars(
            select(AuditLog)
            .where(AuditLog.object_type == "entity", AuditLog.object_id == str(selected.id))
            .order_by(AuditLog.id.desc())
            .limit(50)
        ).all()
    return {
        "entities": entities,
        "selected": selected,
        "entity_types": list_types(session),
        "tag_nodes": tag_nodes,
        "membership": membership,
        "parents": sorted(index.values(), key=lambda n: n.path.casefold()),
        "history": history,
        "format_attributes": format_attributes,
        "message": message,
        "error": error,
        "form": form or {},
    }


def _workspace(request: Request, session: Session, selected_id, **kwargs) -> HTMLResponse:
    return render(
        request, "_entities_workspace.html", workspace_context(session, selected_id, **kwargs)
    )


def _get_entity(session: Session, entity_id: uuid.UUID) -> Entity:
    entity = session.get(Entity, entity_id)
    if entity is None:
        raise DomainError("This entity no longer exists.")
    return entity


def _get_type(session: Session, type_id: str) -> EntityType:
    entity_type = session.get(EntityType, parse_uuid(type_id)) if parse_uuid(type_id) else None
    if entity_type is None:
        raise DomainError("Choose an entity type.")
    return entity_type


def _apply(request, session, selected_id, action, message, form=None):
    try:
        result_id = action()
        session.commit()
        return _workspace(request, session, result_id or selected_id, message=message)
    except (DomainError, ValueError) as exc:
        session.rollback()
        return _workspace(request, session, selected_id, error=str(exc), form=form)


@router.get("/entities", response_class=HTMLResponse)
def entities_page(request: Request, session: SessionDep, id: str | None = None):
    return render(request, "entities.html", workspace_context(session, parse_uuid(id)))


@router.post("/entities/create", response_class=HTMLResponse)
def entities_create(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    type_id: Annotated[str, Form()] = "",
):
    return _apply(
        request,
        session,
        None,
        lambda: create_entity(session, _get_type(session, type_id), name).id,
        N_("Entity created."),
    )


@router.post("/entities/{entity_id}/update", response_class=HTMLResponse)
def entities_update(
    request: Request,
    session: SessionDep,
    entity_id: uuid.UUID,
    name: Annotated[str, Form()],
    type_id: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
    attributes: Annotated[str, Form()] = "",
):
    def action():
        entity = _get_entity(session, entity_id)
        update_entity(
            session,
            entity,
            name=name,
            type_=_get_type(session, type_id),
            aliases=parse_lines(aliases),
            attributes=parse_attributes(attributes),
        )
        return entity.id

    form = {"name": name, "aliases": aliases, "attributes": attributes}
    return _apply(request, session, entity_id, action, N_("Entity saved."), form)


@router.post("/entities/{entity_id}/add-tag", response_class=HTMLResponse)
def entities_add_tag(
    request: Request, session: SessionDep, entity_id: uuid.UUID, parent_id: Annotated[str, Form()]
):
    def action():
        parent = session.get(Tag, parse_uuid(parent_id)) if parse_uuid(parent_id) else None
        if parent is None:
            raise DomainError("Choose where to add the tag.")
        create_tag(session, parent=parent, entity=_get_entity(session, entity_id))
        return entity_id

    return _apply(request, session, entity_id, action, N_("Tag created."))


@router.post("/entities/{entity_id}/delete", response_class=HTMLResponse)
def entities_delete(request: Request, session: SessionDep, entity_id: uuid.UUID):
    def action():
        delete_entity(session, _get_entity(session, entity_id))
        return None

    return _apply(request, session, entity_id, action, N_("Entity deleted."))


# --- entity types ----------------------------------------------------------------------


def _type_action(request, session, selected_id: str, change, message: str):
    """Change a type, then show the entities page with the same entity still selected."""

    def action():
        change()
        return parse_uuid(selected_id)

    return _apply(request, session, parse_uuid(selected_id), action, message)


@router.post("/entities/types/create", response_class=HTMLResponse)
def entity_type_create(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()],
    selected_id: Annotated[str, Form()] = "",
):
    return _type_action(
        request,
        session,
        selected_id,
        lambda: create_type(session, name),
        N_("Entity type created."),
    )


@router.post("/entities/types/{type_id}/rename", response_class=HTMLResponse)
def entity_type_rename(
    request: Request,
    session: SessionDep,
    type_id: uuid.UUID,
    name: Annotated[str, Form()],
    selected_id: Annotated[str, Form()] = "",
):
    return _type_action(
        request,
        session,
        selected_id,
        lambda: rename_type(session, _get_type(session, str(type_id)), name),
        N_("Entity type renamed."),
    )


@router.post("/entities/types/{type_id}/delete", response_class=HTMLResponse)
def entity_type_delete(
    request: Request,
    session: SessionDep,
    type_id: uuid.UUID,
    selected_id: Annotated[str, Form()] = "",
):
    return _type_action(
        request,
        session,
        selected_id,
        lambda: delete_type(session, _get_type(session, str(type_id))),
        N_("Entity type deleted."),
    )


# --- membership ------------------------------------------------------------------------


def membership_context(session: Session, entity: Entity, entities) -> dict:
    """Direct groups/members, indirect ones, and which entities can be added without a loop."""
    by_id = {e.id: e for e in entities}
    direct_groups = set(
        session.scalars(
            select(EntityMembership.group_id).where(EntityMembership.member_id == entity.id)
        )
    )
    direct_members = set(
        session.scalars(
            select(EntityMembership.member_id).where(EntityMembership.group_id == entity.id)
        )
    )
    all_groups, all_members = groups_of(session, {entity.id}), members_of(session, {entity.id})

    def named(ids):
        return sorted((by_id[i] for i in ids), key=lambda e: e.normalized_name)

    return {
        "groups": named(direct_groups),
        "indirect_groups": named(all_groups - direct_groups),
        "members": named(direct_members),
        "indirect_members": named(all_members - direct_members),
        # a group that is already below this entity would create a loop, and vice versa
        "group_options": named(set(by_id) - {entity.id} - all_members - direct_groups),
        "member_options": named(set(by_id) - {entity.id} - all_groups - direct_members),
    }


def _membership_action(request, session, entity_id, other_id: str, change, message):
    def action():
        entity = _get_entity(session, entity_id)
        other = session.get(Entity, parse_uuid(other_id)) if parse_uuid(other_id) else None
        if other is None:
            raise DomainError("Choose an entity.")
        change(entity, other)
        return entity_id

    return _apply(request, session, entity_id, action, message)


@router.post("/entities/{entity_id}/groups/add", response_class=HTMLResponse)
def entity_join_group(
    request: Request, session: SessionDep, entity_id: uuid.UUID, other_id: Annotated[str, Form()]
):
    return _membership_action(
        request,
        session,
        entity_id,
        other_id,
        lambda entity, group: add_membership(session, entity, group),
        N_("Added to the group."),
    )


@router.post("/entities/{entity_id}/groups/{other_id}/remove", response_class=HTMLResponse)
def entity_leave_group(request: Request, session: SessionDep, entity_id: uuid.UUID, other_id: str):
    return _membership_action(
        request,
        session,
        entity_id,
        other_id,
        lambda entity, group: remove_membership(session, entity, group),
        N_("Removed from the group."),
    )


@router.post("/entities/{entity_id}/members/add", response_class=HTMLResponse)
def entity_add_member(
    request: Request, session: SessionDep, entity_id: uuid.UUID, other_id: Annotated[str, Form()]
):
    return _membership_action(
        request,
        session,
        entity_id,
        other_id,
        lambda entity, member: add_membership(session, member, entity),
        N_("Member added."),
    )


@router.post("/entities/{entity_id}/members/{other_id}/remove", response_class=HTMLResponse)
def entity_remove_member(
    request: Request, session: SessionDep, entity_id: uuid.UUID, other_id: str
):
    return _membership_action(
        request,
        session,
        entity_id,
        other_id,
        lambda entity, member: remove_membership(session, member, entity),
        N_("Member removed."),
    )
