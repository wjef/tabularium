"""Global change history (see docs/requirements.md §6)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.models import AuditLog, Document, Entity
from tabularium.web.deps import get_session, render
from tabularium.web.routes_tags import build_tree

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
LIMIT = 200


def _labels(session: Session, entries: list[AuditLog]) -> dict[tuple[str, str], tuple[str, str]]:
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
    if ids := wanted.get("document"):
        for doc in session.scalars(select(Document).where(Document.id.in_(ids))):
            labels[("document", str(doc.id))] = (doc.title or doc.original_filename, "")
    return labels


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, session: SessionDep):
    entries = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(LIMIT)).all()
    return render(
        request,
        "history.html",
        {"entries": entries, "labels": _labels(session, entries), "limit": LIMIT},
    )
