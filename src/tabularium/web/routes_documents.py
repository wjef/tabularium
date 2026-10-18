"""Tagging documents: the "To be tagged" inbox, document page, preview, details, bulk tagging.

Mutations on one document return its metadata block (tags, details, history) so the preview
is not reloaded; in the inbox the document list is refreshed out-of-band alongside.
"""

import tempfile
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium import desktop, paths
from tabularium.desktop import DesktopError
from tabularium.export import make_open_copy, write_zip
from tabularium.i18n import N_
from tabularium.models import AuditLog, Document, DocumentTag, Entity, Tag
from tabularium.services import DomainError
from tabularium.services.documents import (
    add_tag,
    bulk_add_tag,
    remove_tag,
    set_document_date,
    set_retain_until,
    untagged_documents,
    update_details,
)
from tabularium.services.tags import (
    create_tag,
    find_by_path,
    get_or_create_entity_tag,
    tag_paths,
)
from tabularium.web.deps import get_session, parse_uuid, render
from tabularium.web.files import file_response, preview_kind

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
ViewField = Annotated[str, Form()]
INBOX, DOCUMENT = "inbox", "document"


def _parse_date(value: str, label: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DomainError(f"{label}: '{value}' is not a valid date (YYYY-MM-DD).") from exc


def _get_document(session: Session, document_id: uuid.UUID) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _require_tag(session: Session, tag_id: uuid.UUID | None) -> Tag:
    tag = session.get(Tag, tag_id) if tag_id else None
    if tag is None:
        raise DomainError("Choose a tag.")
    return tag


def _resolve_path(session: Session, path: str) -> Tag:
    tag = find_by_path(session, path)
    if tag is None:
        raise DomainError(f"No tag '{path.strip()}'. Pick one from the list or create it below.")
    return tag


def inbox_documents(session: Session) -> list[Document]:
    return list(session.scalars(untagged_documents()))


def meta_context(
    request: Request,
    session: Session,
    document: Document,
    view: str,
    *,
    message: str | None = None,
    error: str | None = None,
) -> dict:
    paths = tag_paths(session)
    assigned_ids = session.scalars(
        select(DocumentTag.tag_id).where(DocumentTag.document_id == document.id)
    ).all()
    assigned = sorted(((paths[t], t) for t in assigned_ids), key=lambda x: x[0].casefold())
    history = session.scalars(
        select(AuditLog)
        .where(AuditLog.object_type == "document", AuditLog.object_id == str(document.id))
        .order_by(AuditLog.id.desc())
        .limit(50)
    ).all()
    inbox = inbox_documents(session) if view == INBOX else []
    next_doc = next((d for d in inbox if d.id != document.id), None)
    return {
        "doc": document,
        "view": view,
        "assigned": assigned,
        "all_paths": sorted(paths.values(), key=str.casefold),
        "parents": sorted(((p, t) for t, p in paths.items()), key=lambda x: x[0].casefold()),
        "entities": session.scalars(select(Entity).order_by(Entity.normalized_name)).all(),
        "history": history,
        "inbox": inbox,
        "next_doc": next_doc,
        "retention_years": request.app.state.config.retention.years,
        "message": message,
        "error": error,
    }


def page_context(request: Request, session: Session, document: Document | None, view: str):
    if document is None:
        return {"doc": None, "view": view, "inbox": inbox_documents(session), "preview": "none"}
    context = meta_context(request, session, document, view)
    context["preview"] = preview_kind(document)
    return context


def _meta(request: Request, session: Session, document_id: uuid.UUID, view: str, action, message):
    """Run a change on one document and return its refreshed metadata block."""
    document = _get_document(session, document_id)
    try:
        action(document)
        session.commit()
        context = meta_context(request, session, document, view, message=message)
    except (DomainError, ValueError) as exc:
        session.rollback()
        context = meta_context(request, session, document, view, error=str(exc))
    context["oob"] = view == INBOX
    return render(request, "_doc_meta.html", context)


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, session: SessionDep, id: str | None = None):
    selected = session.get(Document, parse_uuid(id)) if parse_uuid(id) else None
    if selected is None:
        selected = next(iter(inbox_documents(session)), None)
    return render(request, "inbox.html", page_context(request, session, selected, INBOX))


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_page(request: Request, session: SessionDep, document_id: uuid.UUID):
    document = _get_document(session, document_id)
    return render(request, "document.html", page_context(request, session, document, DOCUMENT))


@router.get("/documents/{document_id}/file")
def document_file(
    request: Request, session: SessionDep, document_id: uuid.UUID, download: bool = False
):
    document = _get_document(session, document_id)
    storage = request.app.state.storage
    if not storage.exists(document.stored_path):
        raise HTTPException(status_code=404, detail="File is missing from the archive")
    return file_response(storage, document, download=download)


@router.post("/documents/{document_id}/tags/add", response_class=HTMLResponse)
def add_existing_tag(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    tag_path: Annotated[str, Form()],
    view: ViewField = DOCUMENT,
):
    def action(document):
        tag = _resolve_path(session, tag_path)
        if not add_tag(session, document, tag):
            raise DomainError("The document already has this tag.")

    return _meta(request, session, document_id, view, action, N_("Tag added."))


@router.post("/documents/{document_id}/tags/new", response_class=HTMLResponse)
def add_new_tag(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    name: Annotated[str, Form()],
    parent_id: Annotated[str, Form()] = "",
    view: ViewField = DOCUMENT,
):
    def action(document):
        parent = _require_tag(session, parse_uuid(parent_id)) if parse_uuid(parent_id) else None
        add_tag(session, document, create_tag(session, name, parent=parent))

    return _meta(request, session, document_id, view, action, N_("Tag created and added."))


@router.post("/documents/{document_id}/tags/entity", response_class=HTMLResponse)
def add_entity_tag(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    parent_id: Annotated[str, Form()] = "",
    entity_id: Annotated[str, Form()] = "",
    view: ViewField = DOCUMENT,
):
    def action(document):
        parent = _require_tag(session, parse_uuid(parent_id))
        entity = session.get(Entity, parse_uuid(entity_id)) if parse_uuid(entity_id) else None
        if entity is None:
            raise DomainError("Choose an entity.")
        add_tag(session, document, get_or_create_entity_tag(session, parent, entity))

    return _meta(request, session, document_id, view, action, N_("Tag added."))


@router.post("/documents/{document_id}/tags/{tag_id}/remove", response_class=HTMLResponse)
def remove_document_tag(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    tag_id: uuid.UUID,
    view: ViewField = DOCUMENT,
):
    def action(document):
        remove_tag(session, document, _require_tag(session, tag_id))

    return _meta(request, session, document_id, view, action, N_("Tag removed."))


@router.post("/documents/{document_id}/details", response_class=HTMLResponse)
def save_details(
    request: Request,
    session: SessionDep,
    document_id: uuid.UUID,
    title: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    document_date: Annotated[str, Form()] = "",
    retain_until: Annotated[str, Form()] = "",
    retain_auto: Annotated[str, Form()] = "",
    view: ViewField = DOCUMENT,
):
    years = request.app.state.config.retention.years

    def action(document):
        update_details(session, document, title=title, note=note)
        set_document_date(
            session,
            document,
            _parse_date(document_date, "Document date"),
            retention_years=years,
        )
        if retain_auto:
            set_retain_until(session, document, None, retention_years=years)
        else:
            value = _parse_date(retain_until, "Keep until")
            if value is None:
                raise DomainError("Keep until: enter a date or tick 'automatic'.")
            set_retain_until(session, document, value, retention_years=years)

    return _meta(request, session, document_id, view, action, N_("Details saved."))


@router.post("/documents/bulk-tag", response_class=HTMLResponse)
def bulk_tag(
    request: Request,
    session: SessionDep,
    tag_path: Annotated[str, Form()] = "",
    doc_ids: Annotated[list[str] | None, Form()] = None,
    selected_id: Annotated[str, Form()] = "",
):
    ids = [i for i in (parse_uuid(v) for v in doc_ids or []) if i is not None]
    message = error = None
    try:
        if not ids:
            raise DomainError("Select at least one document.")
        tag = _resolve_path(session, tag_path)
        documents = list(session.scalars(select(Document).where(Document.id.in_(ids))))
        added = bulk_add_tag(session, documents, tag)
        session.commit()
        message = f"{added}"
    except DomainError as exc:
        session.rollback()
        error = str(exc)
    paths = tag_paths(session)
    return render(
        request,
        "_inbox_list.html",
        {
            "inbox": inbox_documents(session),
            "selected_id": parse_uuid(selected_id),
            "all_paths": sorted(paths.values(), key=str.casefold),
            "bulk_added": message,
            "list_error": error,
        },
    )


# --- export, open, show ---------------------------------------------------------------


def document_tag_paths(
    session: Session, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Document id → sorted tag paths, for many documents in one query."""
    paths = tag_paths(session)
    result: dict[uuid.UUID, list[str]] = {i: [] for i in document_ids}
    rows = session.execute(
        select(DocumentTag.document_id, DocumentTag.tag_id).where(
            DocumentTag.document_id.in_(document_ids)
        )
    )
    for document_id, tag_id in rows:
        result[document_id].append(paths[tag_id])
    for value in result.values():
        value.sort(key=str.casefold)
    return result


@router.post("/documents/export")
def export_documents(
    request: Request,
    session: SessionDep,
    doc_ids: Annotated[list[str] | None, Form()] = None,
):
    """Selected documents as a zip of readable copies (plus index.csv)."""
    ids = [i for i in (parse_uuid(v) for v in doc_ids or []) if i is not None]
    if not ids:
        raise HTTPException(status_code=400, detail="Select at least one document.")
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.id.in_(ids))
            .order_by(Document.document_date, Document.original_filename)
        )
    )
    storage = request.app.state.storage
    missing = [d.original_filename for d in documents if not storage.exists(d.stored_path)]
    if missing:
        raise HTTPException(status_code=409, detail=f"Missing from the archive: {missing}")

    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115
    write_zip(storage, documents, document_tag_paths(session, ids), spool)
    size = spool.tell()
    spool.seek(0)

    def chunks():
        try:
            while chunk := spool.read(1024 * 1024):
                yield chunk
        finally:
            spool.close()

    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="Tabularium export {stamp}.zip"',
            "Content-Length": str(size),
        },
    )


def _desktop_action(request: Request, session: Session, document_id: uuid.UUID, action):
    document = _get_document(session, document_id)
    try:
        message = action(document, request.app.state.storage)
        return render(request, "_flash.html", {"message": message, "error": None})
    except (DesktopError, OSError) as exc:
        return render(request, "_flash.html", {"message": None, "error": str(exc)})


@router.post("/documents/{document_id}/open", response_class=HTMLResponse)
def open_document(request: Request, session: SessionDep, document_id: uuid.UUID):
    def action(document, storage):
        if desktop.is_runnable(document.original_filename):
            raise DesktopError(
                "Programs and scripts are not opened from the archive. "
                "Use Download or Show in Explorer if you really need the file."
            )
        copy = make_open_copy(storage, document, paths.app_home() / "tmp" / "open")
        desktop.open_with_default_app(copy)
        return N_("Opened a read-only copy; changes to it are not saved in the archive.")

    return _desktop_action(request, session, document_id, action)


@router.post("/documents/{document_id}/reveal", response_class=HTMLResponse)
def reveal_document(request: Request, session: SessionDep, document_id: uuid.UUID):
    def action(document, storage):
        local_path = getattr(storage, "local_path", None)
        if local_path is None:
            raise DesktopError("This archive is not on a local disk.")
        desktop.show_in_explorer(local_path(document.stored_path))
        return N_("Shown in Explorer (archived files have hash names).")

    return _desktop_action(request, session, document_id, action)
