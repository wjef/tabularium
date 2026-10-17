"""Tag tree management: create, rename, move, merge, delete (see docs/adr/0004).

Selection uses plain links (``/tags?id=…``); every change is an htmx POST that returns the
whole workspace (tree + panel), so the page always shows the current state.
"""

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.i18n import N_
from tabularium.models import AuditLog, Entity, Tag
from tabularium.services import DomainError
from tabularium.services.tags import (
    create_tag,
    delete_tag,
    descendant_ids,
    document_counts,
    merge_tag,
    move_tag,
    rename_tag,
)
from tabularium.web.deps import get_session, parse_uuid, render

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@dataclass
class Node:
    tag: Tag
    count: int
    path: str
    children: list["Node"] = field(default_factory=list)


def build_tree(session: Session) -> tuple[list[Node], dict[uuid.UUID, Node]]:
    """All tags as a tree, sorted by sort order then name, plus an id → node index."""
    tags = session.scalars(select(Tag).order_by(Tag.sort_order, Tag.normalized_name)).all()
    counts = document_counts(session)
    by_parent: dict[uuid.UUID | None, list[Tag]] = {}
    for tag in tags:
        by_parent.setdefault(tag.parent_id, []).append(tag)

    index: dict[uuid.UUID, Node] = {}

    def build(parent_id: uuid.UUID | None, prefix: str) -> list[Node]:
        nodes = []
        for tag in by_parent.get(parent_id, []):
            path = f"{prefix} / {tag.name}" if prefix else tag.name
            node = Node(tag, counts.get(tag.id, 0), path)
            index[tag.id] = node
            node.children = build(tag.id, path)
            nodes.append(node)
        return nodes

    return build(None, ""), index


def workspace_context(
    session: Session,
    selected_id: uuid.UUID | None,
    *,
    message: str | None = None,
    error: str | None = None,
) -> dict:
    roots, index = build_tree(session)
    selected = index.get(selected_id) if selected_id else None
    history, targets = [], list(index.values())
    if selected is not None:
        excluded = descendant_ids(session, [selected.tag.id])
        targets = [n for n in index.values() if n.tag.id not in excluded]
        history = session.scalars(
            select(AuditLog)
            .where(AuditLog.object_type == "tag", AuditLog.object_id == str(selected.tag.id))
            .order_by(AuditLog.id.desc())
            .limit(50)
        ).all()
    entities = session.scalars(select(Entity).order_by(Entity.normalized_name)).all()
    return {
        "roots": roots,
        "all_nodes": sorted(index.values(), key=lambda n: n.path.casefold()),
        "selected": selected,
        "targets": sorted(targets, key=lambda n: n.path.casefold()),
        "entities": entities,
        "history": history,
        "message": message,
        "error": error,
    }


def _workspace(request: Request, session: Session, selected_id, **kwargs) -> HTMLResponse:
    return render(
        request, "_tags_workspace.html", workspace_context(session, selected_id, **kwargs)
    )


def _apply(request: Request, session: Session, selected_id, action, message: str):
    """Run a service call; commit on success, show the domain error otherwise."""
    try:
        result_id = action()
        session.commit()
        return _workspace(request, session, result_id or selected_id, message=message)
    except (DomainError, ValueError) as exc:
        session.rollback()
        return _workspace(request, session, selected_id, error=str(exc))


def _get_tag(session: Session, tag_id: uuid.UUID) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise DomainError("This tag no longer exists.")
    return tag


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request, session: SessionDep, id: str | None = None):
    return render(request, "tags.html", workspace_context(session, parse_uuid(id)))


@router.post("/tags/create", response_class=HTMLResponse)
def tags_create(
    request: Request,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
    entity_id: Annotated[str, Form()] = "",
):
    def action():
        parent = _get_tag(session, parse_uuid(parent_id)) if parse_uuid(parent_id) else None
        entity = session.get(Entity, parse_uuid(entity_id)) if parse_uuid(entity_id) else None
        return create_tag(session, name.strip() or None, parent=parent, entity=entity).id

    return _apply(request, session, parse_uuid(parent_id), action, N_("Tag created."))


@router.post("/tags/{tag_id}/rename", response_class=HTMLResponse)
def tags_rename(
    request: Request, session: SessionDep, tag_id: uuid.UUID, name: Annotated[str, Form()]
):
    return _apply(
        request,
        session,
        tag_id,
        lambda: rename_tag(session, _get_tag(session, tag_id), name).id,
        N_("Tag renamed."),
    )


@router.post("/tags/{tag_id}/move", response_class=HTMLResponse)
def tags_move(
    request: Request, session: SessionDep, tag_id: uuid.UUID, parent_id: Annotated[str, Form()] = ""
):
    def action():
        parent = _get_tag(session, parse_uuid(parent_id)) if parse_uuid(parent_id) else None
        return move_tag(session, _get_tag(session, tag_id), parent).id

    return _apply(request, session, tag_id, action, N_("Tag moved."))


@router.post("/tags/{tag_id}/merge", response_class=HTMLResponse)
def tags_merge(
    request: Request, session: SessionDep, tag_id: uuid.UUID, target_id: Annotated[str, Form()]
):
    def action():
        target = _get_tag(session, parse_uuid(target_id)) if parse_uuid(target_id) else None
        if target is None:
            raise DomainError("Choose a tag to merge into.")
        merge_tag(session, _get_tag(session, tag_id), target)
        return target.id

    return _apply(request, session, tag_id, action, N_("Tags merged."))


@router.post("/tags/{tag_id}/delete", response_class=HTMLResponse)
def tags_delete(request: Request, session: SessionDep, tag_id: uuid.UUID):
    def action():
        tag = _get_tag(session, tag_id)
        parent_id = tag.parent_id
        delete_tag(session, tag)
        return parent_id

    return _apply(request, session, tag_id, action, N_("Tag deleted."))
