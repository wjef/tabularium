"""Outdating from the document page; the read-only Outdated section; restore to active.

The Outdated section shows what an outdated document was (tags, entities, relations,
history from its snapshot) and lets you preview, download or restore it — nothing else.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.outdated import OutdatedDocument, OutdatedEvent
from tabularium.services import DomainError
from tabularium.services.outdating import outdate, restore_to_active
from tabularium.text import normalize
from tabularium.web.deps import get_session, parse_uuid, render
from tabularium.web.files import file_response, preview_kind
from tabularium.web.routes_documents import DOCUMENT, _get_document, meta_context

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def _redirect(url: str) -> Response:
    """htmx follows HX-Redirect with a full page load."""
    return Response(status_code=204, headers={"HX-Redirect": url})


@router.post("/documents/{document_id}/outdate", response_class=HTMLResponse)
def outdate_document(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    reason: Annotated[str, Form()] = "",
):
    document = _get_document(session, document_id)
    state = request.app.state
    try:
        outdate(
            session,
            state.storage,
            state.outdated_store,
            document,
            today=date.today(),
            reason=reason,
        )
    except (DomainError, OSError) as exc:
        session.rollback()
        context = meta_context(request, session, document, DOCUMENT, error=str(exc))
        return render(request, "_doc_meta.html", context)
    return _redirect(f"/outdated?id={document_id}")


def _matches(row: OutdatedDocument, words: list[str]) -> bool:
    haystack = normalize(
        " ".join(
            [row.title or "", row.original_filename, row.note or "", row.sha256, row.reason or ""]
            + row.tag_paths
        )
    )
    return all(w in haystack for w in words)


@router.get("/outdated", response_class=HTMLResponse)
def outdated_page(request: Request, q: str = "", id: str = "", error: str | None = None):
    store = request.app.state.outdated_store
    words = normalize(q).split()
    with store.session_factory() as outdated:
        rows = outdated.scalars(
            select(OutdatedDocument).order_by(OutdatedDocument.outdated_at.desc())
        ).all()
        results = [r for r in rows if _matches(r, words)]
        selected = outdated.get(OutdatedDocument, parse_uuid(id)) if parse_uuid(id) else None
        events = (
            outdated.scalars(
                select(OutdatedEvent)
                .where(OutdatedEvent.document_id == str(selected.id))
                .order_by(OutdatedEvent.id)
            ).all()
            if selected
            else []
        )
    return render(
        request,
        "outdated.html",
        {
            "q": q,
            "results": results,
            "total": len(rows),
            "doc": selected,
            "preview": preview_kind(selected) if selected else "none",
            "events": events,
            "error": error,
            "message": None,
        },
    )


@router.get("/outdated/{document_id}/file")
def outdated_file(request: Request, document_id: uuid.UUID, download: bool = False):
    store = request.app.state.outdated_store
    with store.session_factory() as outdated:
        row = outdated.get(OutdatedDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not in the outdated store")
    if not store.storage.exists(row.stored_path):
        raise HTTPException(status_code=404, detail="File is missing from the Outdated folder")
    return file_response(store.storage, row, download=download)


@router.post("/outdated/{document_id}/restore", response_class=HTMLResponse)
def restore_document(request: Request, session: SessionDep, document_id: uuid.UUID):
    state = request.app.state
    try:
        restore_to_active(session, state.storage, state.outdated_store, document_id)
    except (DomainError, OSError) as exc:
        session.rollback()
        return outdated_page(request, id=str(document_id), error=str(exc))
    return _redirect(f"/documents/{document_id}")
