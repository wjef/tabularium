"""Entity management: people and organizations with their details (see docs/adr/0004)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.i18n import N_
from tabularium.models import AuditLog, Entity, EntityType, Tag
from tabularium.services import DomainError
from tabularium.services.entities import create_entity, delete_entity, update_entity
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
    _, index = build_tree(session)
    if selected is not None:
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
        "entity_types": list(EntityType),
        "tag_nodes": tag_nodes,
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
    type: Annotated[EntityType, Form()],
):
    return _apply(
        request,
        session,
        None,
        lambda: create_entity(session, type, name).id,
        N_("Entity created."),
    )


@router.post("/entities/{entity_id}/update", response_class=HTMLResponse)
def entities_update(
    request: Request,
    session: SessionDep,
    entity_id: uuid.UUID,
    name: Annotated[str, Form()],
    type: Annotated[EntityType, Form()],
    aliases: Annotated[str, Form()] = "",
    attributes: Annotated[str, Form()] = "",
):
    def action():
        entity = _get_entity(session, entity_id)
        update_entity(
            session,
            entity,
            name=name,
            type_=type,
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
