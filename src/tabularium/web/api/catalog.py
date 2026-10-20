"""Reference data for the front end: app info, tags, entities, entity types, history."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tabularium import __version__
from tabularium.models import AuditLog, Document, Entity, EntityType, Tag
from tabularium.outdated import OutdatedDocument
from tabularium.services.documents import inbox_documents
from tabularium.services.entity_types import list_types
from tabularium.services.tags import document_counts, subtree_document_counts, tag_paths
from tabularium.web.api import schemas as s
from tabularium.web.deps import get_session
from tabularium.web.security import CSRF_HEADER

router = APIRouter(tags=["catalog"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/info")
def info(request: Request, session: SessionDep) -> dict:
    """Small facts for the shell: version, counts for the sidebar, language."""
    state = request.app.state
    inbox = session.scalar(select(func.count()).select_from(inbox_documents().subquery()))
    return {
        "version": __version__,
        "language": state.config.ui.language,
        "inbox": inbox or 0,
        "outdated": state.outdated_store.count(),
        "restore_pending": state.restore_pending(),
    }


@router.get("/tags", response_model=list[s.TagNode])
def tags(session: SessionDep):
    """All tags (flat, parents before children, by sort order then name)."""
    paths = tag_paths(session)
    counts = document_counts(session)
    totals = subtree_document_counts(session, done_only=True)
    rows = session.scalars(select(Tag).order_by(Tag.sort_order, Tag.normalized_name)).all()
    nodes = [
        s.TagNode(
            id=t.id,
            name=t.name,
            path=paths[t.id],
            parent_id=t.parent_id,
            entity_id=t.entity_id,
            color=t.color,
            sort_order=t.sort_order,
            count=counts.get(t.id, 0),
            total=totals.get(t.id, 0),
        )
        for t in rows
    ]
    return sorted(nodes, key=lambda n: (n.path.count(" / "), n.sort_order, n.path.casefold()))


@router.get("/entities", response_model=list[s.EntityOut])
def entities(session: SessionDep):
    tag_counts = dict(
        session.execute(
            select(Tag.entity_id, func.count())
            .where(Tag.entity_id.is_not(None))
            .group_by(Tag.entity_id)
        ).all()
    )
    rows = session.scalars(select(Entity).order_by(Entity.normalized_name)).all()
    return [
        s.EntityOut(
            id=e.id,
            name=e.name,
            type_id=e.type_id,
            type_name=e.type.name,
            aliases=e.aliases,
            attributes=e.attributes,
            tag_count=tag_counts.get(e.id, 0),
        )
        for e in rows
    ]


@router.get("/entity-types", response_model=list[s.EntityTypeOut])
def entity_types(session: SessionDep):
    counts = dict(
        session.execute(select(Entity.type_id, func.count()).group_by(Entity.type_id)).all()
    )
    return [
        s.EntityTypeOut(
            id=t.id, name=t.name, sort_order=t.sort_order, entity_count=counts.get(t.id, 0)
        )
        for t in list_types(session)
    ]


@router.get("/history", response_model=list[s.HistoryEntry])
def history(request: Request, session: SessionDep, limit: int = 200):
    entries = session.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    ).all()
    labels = history_labels(session, entries, request.app.state.outdated_store)
    result = []
    for e in entries:
        entry = s.HistoryEntry.model_validate(e)
        label = labels.get((e.object_type, e.object_id))
        if label:
            entry.label, entry.link = label[0], label[1] or None
        result.append(entry)
    return result


@router.get("/session")
def session_info(request: Request) -> dict:
    """The CSRF header the front end must send with every change.

    Safe to expose: other websites cannot read this response (no CORS), and DNS-rebinding
    is stopped by the Host allow-list.
    """
    return {"csrf_header": CSRF_HEADER, "csrf_token": request.app.state.csrf_token}


def history_labels(
    session: Session, entries: list[AuditLog], outdated_store=None
) -> dict[tuple[str, str], tuple[str, str]]:
    """(object_type, object_id) → (label, link in the app) for objects that still exist."""
    wanted: dict[str, set[uuid.UUID]] = {}
    for e in entries:
        try:
            wanted.setdefault(e.object_type, set()).add(uuid.UUID(e.object_id))
        except ValueError:
            continue
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    if ids := wanted.get("tag"):
        paths = tag_paths(session)
        for tag_id in ids:
            if tag_id in paths:
                labels[("tag", str(tag_id))] = (paths[tag_id], f"/tags?id={tag_id}")
    if ids := wanted.get("entity"):
        for entity in session.scalars(select(Entity).where(Entity.id.in_(ids))):
            labels[("entity", str(entity.id))] = (entity.name, f"/entities?id={entity.id}")
    if ids := wanted.get("entity_type"):
        for entity_type in session.scalars(select(EntityType).where(EntityType.id.in_(ids))):
            labels[("entity_type", str(entity_type.id))] = (entity_type.name, "/entities")
    if ids := wanted.get("document"):
        for doc in session.scalars(select(Document).where(Document.id.in_(ids))):
            labels[("document", str(doc.id))] = (doc.display_name, f"/?doc={doc.id}")
        missing = {i for i in ids if ("document", str(i)) not in labels}
        if missing and outdated_store is not None:
            with outdated_store.session_factory() as outdated:
                rows = outdated.scalars(
                    select(OutdatedDocument).where(OutdatedDocument.id.in_(missing))
                )
                for row in rows:
                    labels[("document", str(row.id))] = (
                        f"{row.display_name} (outdated)",
                        f"/outdated?id={row.id}",
                    )
    return labels
