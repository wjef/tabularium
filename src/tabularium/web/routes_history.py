"""Global change history (see docs/requirements.md §6)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.models import AuditLog, Document, Entity, EntityType
from tabularium.outdated import OutdatedDocument
from tabularium.web.deps import get_session, render
from tabularium.web.routes_tags import build_tree

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
LIMIT = 200


def _labels(
    session: Session, entries: list[AuditLog], outdated_store=None
) -> dict[tuple[str, str], tuple[str, str]]:
    """(object_type, object_id) → (label, link) for objects that still exist."""
    _, tag_index = build_tree(session)
    wanted: dict[str, set[uuid.UUID]] = {}
    for e in entries:
        try:
            wanted.setdefault(e.object_type, set()).add(uuid.UUID(e.object_id))
        except ValueError:
            continue
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for tag_id in wanted.get("tag", ()):
        if tag_id in tag_index:
            labels[("tag", str(tag_id))] = (tag_index[tag_id].path, f"/tags?id={tag_id}")
    if ids := wanted.get("entity"):
        for entity in session.scalars(select(Entity).where(Entity.id.in_(ids))):
            labels[("entity", str(entity.id))] = (entity.name, f"/entities?id={entity.id}")
    if ids := wanted.get("entity_type"):
        for entity_type in session.scalars(select(EntityType).where(EntityType.id.in_(ids))):
            labels[("entity_type", str(entity_type.id))] = (entity_type.name, "/entities")
    if ids := wanted.get("document"):
        for doc in session.scalars(select(Document).where(Document.id.in_(ids))):
            label = doc.display_name
            labels[("document", str(doc.id))] = (label, f"/documents/{doc.id}")
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


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, session: SessionDep):
    entries = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(LIMIT)).all()
    return render(
        request,
        "history.html",
        {
            "entries": entries,
            "labels": _labels(session, entries, request.app.state.outdated_store),
            "limit": LIMIT,
        },
    )
