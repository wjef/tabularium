"""Documents: list/search, detail, edits, tagging, links, text, history, desktop actions."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium import desktop, paths
from tabularium.desktop import DesktopError
from tabularium.export import make_open_copy
from tabularium.i18n import N_
from tabularium.models import AuditLog, Document, DocumentTag, DocumentText, Entity, Tag, TextStatus
from tabularium.retention import can_outdate
from tabularium.services import DomainError
from tabularium.services.documents import (
    add_tag,
    bulk_add_tag,
    remove_tag,
    set_document_date,
    set_retain_until,
    update_details,
)
from tabularium.services.outdating import outdate
from tabularium.services.relations import add_link, links_of, parse_choice, remove_link
from tabularium.services.relations import choices as relation_choices
from tabularium.services.search import SORTS, SearchQuery, search
from tabularium.services.tags import create_tag, get_or_create_entity_tag, tag_paths
from tabularium.text import normalize
from tabularium.textindex import request_extraction, snippet
from tabularium.thumbnails import thumbnail_path
from tabularium.web.api import schemas as s
from tabularium.web.deps import get_session
from tabularium.web.files import file_response, preview_kind

router = APIRouter(tags=["documents"])
SessionDep = Annotated[Session, Depends(get_session)]
MAX_PAGE = 200


# --- building responses -----------------------------------------------------------------


def _get(session: Session, document_id: uuid.UUID) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def summaries(
    session: Session, documents: list[Document], words: list[str] | None = None
) -> list[s.DocumentSummary]:
    """Summaries for many documents with a fixed number of queries."""
    ids = [d.id for d in documents]
    if not ids:
        return []
    paths_by_id = tag_paths(session)
    tags_by_doc: dict[uuid.UUID, list[s.TagRef]] = {i: [] for i in ids}
    rows = session.execute(
        select(DocumentTag.document_id, Tag.id, Tag.name, Tag.entity_id)
        .join(Tag, Tag.id == DocumentTag.tag_id)
        .where(DocumentTag.document_id.in_(ids))
    )
    for doc_id, tag_id, name, entity_id in rows:
        tags_by_doc[doc_id].append(
            s.TagRef(id=tag_id, name=name, path=paths_by_id[tag_id], entity_id=entity_id)
        )
    texts = {
        row.document_id: row
        for row in session.scalars(select(DocumentText).where(DocumentText.document_id.in_(ids)))
    }
    result = []
    for d in documents:
        text = texts.get(d.id)
        result.append(
            s.DocumentSummary(
                id=d.id,
                display_name=d.display_name,
                stored_name=d.stored_name,
                title=d.title,
                document_date=d.document_date,
                ingested_at=d.ingested_at,
                mime_type=d.mime_type,
                size_bytes=d.size_bytes,
                preview=preview_kind(d),
                tags=sorted(tags_by_doc[d.id], key=lambda t: t.path.casefold()),
                snippet=snippet(text.content, words) if words and text and text.content else None,
                text_status=(text.status if text else TextStatus.PENDING).value,
            )
        )
    return result


def detail(request: Request, session: Session, document: Document) -> s.DocumentDetail:
    base = summaries(session, [document])[0]
    text = session.get(DocumentText, document.id)
    links = [
        s.LinkOut(
            relation_id=link.relation.id,
            kind=link.relation.type.value,
            direction="out" if link.relation.from_document_id == document.id else "in",
            phrase=link.phrase,
            other=s.DocRef(
                id=link.other.id,
                display_name=link.other.display_name,
                document_date=link.other.document_date,
            ),
        )
        for link in links_of(session, document)
    ]
    return s.DocumentDetail(
        **base.model_dump(),
        note=document.note,
        sha256=document.sha256,
        stored_path=document.stored_path,
        original_filename=document.original_filename,
        original_relpath=document.original_relpath,
        retain_until=document.retain_until,
        retain_until_manual=document.retain_until_manual,
        can_outdate=can_outdate(document.retain_until, date.today()),
        links=links,
        text=s.TextInfo(
            status=(text.status if text else TextStatus.PENDING).value,
            method=text.method if text else None,
            pages=text.pages if text else None,
            ocr_pages=text.ocr_pages if text else None,
            chars=text.chars if text else None,
            error=text.error if text else None,
        ),
    )


def _changed(request: Request, session: Session, document_id: uuid.UUID) -> s.DocumentDetail:
    """Commit and return the fresh document."""
    session.commit()
    return detail(request, session, _get(session, document_id))


# --- reading ----------------------------------------------------------------------------


@router.get("/documents", response_model=s.DocumentPage)
def list_documents(
    session: SessionDep,
    q: str = "",
    tag: Annotated[list[uuid.UUID] | None, Query()] = None,
    entity: Annotated[list[uuid.UUID] | None, Query()] = None,
    related: bool = False,
    untagged: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "date",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
):
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(SORTS)}")
    query = SearchQuery(
        tag_ids=tag or [],
        entity_ids=entity or [],
        text=q,
        date_from=date_from,
        date_to=date_to,
        include_related=related,
        untagged=untagged,
        sort=sort,
    )
    documents, total = search(session, query, limit=limit, offset=offset)
    words = normalize(q).split()
    return s.DocumentPage(total=total, offset=offset, items=summaries(session, documents, words))


@router.get("/documents/{document_id}", response_model=s.DocumentDetail)
def get_document(request: Request, session: SessionDep, document_id: uuid.UUID):
    return detail(request, session, _get(session, document_id))


@router.get("/documents/{document_id}/text")
def get_text(session: SessionDep, document_id: uuid.UUID) -> dict:
    _get(session, document_id)
    text = session.get(DocumentText, document_id)
    return {"content": text.content if text else None}


@router.get("/documents/{document_id}/file")
def get_file(request: Request, session: SessionDep, document_id: uuid.UUID, download: bool = False):
    """The archived file: shown inline when that is safe (see web.files), else a download."""
    document = _get(session, document_id)
    storage = request.app.state.storage
    if not storage.exists(document.stored_path):
        raise HTTPException(status_code=404, detail="File is missing from the archive")
    return file_response(storage, document, download=download)


@router.get("/documents/{document_id}/thumbnail", response_class=FileResponse)
def get_thumbnail(request: Request, session: SessionDep, document_id: uuid.UUID):
    """Small WebP preview (first PDF page or the photo); 404 when the type has none."""
    document = _get(session, document_id)
    path = thumbnail_path(request.app.state.storage, document)
    if path is None:
        raise HTTPException(status_code=404, detail="No thumbnail for this document")
    # the file behind a document never changes, so browsers may keep the thumbnail
    return FileResponse(
        path, media_type="image/webp", headers={"Cache-Control": "private, max-age=604800"}
    )


@router.get("/documents/{document_id}/history", response_model=list[s.HistoryEntry])
def document_history(session: SessionDep, document_id: uuid.UUID):
    _get(session, document_id)
    return session.scalars(
        select(AuditLog)
        .where(AuditLog.object_type == "document", AuditLog.object_id == str(document_id))
        .order_by(AuditLog.id.desc())
    ).all()


@router.get("/link-choices", response_model=list[s.LinkChoice])
def link_choices():
    return [s.LinkChoice(value=v, phrase=p) for v, p in relation_choices()]


# --- editing ----------------------------------------------------------------------------


@router.patch("/documents/{document_id}", response_model=s.DocumentDetail)
def update_document(
    request: Request, session: SessionDep, document_id: uuid.UUID, body: s.DocumentUpdate
):
    document = _get(session, document_id)
    years = request.app.state.config.retention.years
    sent = body.model_fields_set
    if "title" in sent or "note" in sent:
        update_details(
            session,
            document,
            title=body.title if "title" in sent else None,
            note=body.note if "note" in sent else None,
        )
    if "document_date" in sent or body.clear_document_date:
        set_document_date(
            session,
            document,
            None if body.clear_document_date else body.document_date,
            retention_years=years,
        )
    if body.retain_auto:
        set_retain_until(session, document, None, retention_years=years)
    elif body.retain_until is not None:
        set_retain_until(session, document, body.retain_until, retention_years=years)
    return _changed(request, session, document_id)


@router.post("/documents/{document_id}/tags", response_model=s.DocumentDetail)
def assign_tag(request: Request, session: SessionDep, document_id: uuid.UUID, body: s.TagAssign):
    document = _get(session, document_id)
    parent = session.get(Tag, body.parent_id) if body.parent_id else None
    if body.parent_id and parent is None:
        raise DomainError("The parent tag no longer exists.")
    if body.tag_id:
        tag = session.get(Tag, body.tag_id)
        if tag is None:
            raise DomainError("This tag no longer exists.")
    elif body.entity_id:
        entity = session.get(Entity, body.entity_id)
        if entity is None or parent is None:
            raise DomainError("Choose an entity and where to put it.")
        tag = get_or_create_entity_tag(session, parent, entity)
    elif body.name:
        tag = create_tag(session, body.name, parent=parent)
    else:
        raise DomainError("Choose a tag.")
    if not add_tag(session, document, tag):
        raise DomainError("The document already has this tag.")
    return _changed(request, session, document_id)


@router.delete("/documents/{document_id}/tags/{tag_id}", response_model=s.DocumentDetail)
def unassign_tag(request: Request, session: SessionDep, document_id: uuid.UUID, tag_id: uuid.UUID):
    document = _get(session, document_id)
    tag = session.get(Tag, tag_id)
    if tag is None or not remove_tag(session, document, tag):
        raise DomainError("The document does not have this tag.")
    return _changed(request, session, document_id)


@router.post("/documents/bulk-tags", response_model=s.Message)
def bulk_tags(session: SessionDep, body: s.BulkTag):
    tag = session.get(Tag, body.tag_id)
    if tag is None:
        raise DomainError("This tag no longer exists.")
    documents = list(session.scalars(select(Document).where(Document.id.in_(body.document_ids))))
    added = bulk_add_tag(session, documents, tag)
    session.commit()
    return s.Message(message=f"{added}")


@router.post("/documents/{document_id}/links", response_model=s.DocumentDetail)
def create_link(request: Request, session: SessionDep, document_id: uuid.UUID, body: s.LinkCreate):
    document = _get(session, document_id)
    other = session.get(Document, body.other_id)
    if other is None:
        raise DomainError("The other document no longer exists.")
    kind, direction = parse_choice(body.kind)
    add_link(session, document, other, kind, direction)
    return _changed(request, session, document_id)


@router.delete("/documents/{document_id}/links/{relation_id}", response_model=s.DocumentDetail)
def delete_link(
    request: Request, session: SessionDep, document_id: uuid.UUID, relation_id: uuid.UUID
):
    _get(session, document_id)
    remove_link(session, relation_id)
    return _changed(request, session, document_id)


@router.post("/documents/{document_id}/extract", response_model=s.DocumentDetail)
def extract_again(request: Request, session: SessionDep, document_id: uuid.UUID):
    _get(session, document_id)
    request_extraction(session, document_id)
    result = _changed(request, session, document_id)
    request.app.state.text_indexer.wake()
    return result


@router.post("/documents/{document_id}/outdate", response_model=s.Message)
def outdate_document(
    request: Request, session: SessionDep, document_id: uuid.UUID, body: s.OutdateRequest
):
    state = request.app.state
    outdate(
        session,
        state.storage,
        state.outdated_store,
        _get(session, document_id),
        today=date.today(),
        reason=body.reason,
    )
    return s.Message(message=N_("Document outdated."))


@router.post("/documents/{document_id}/open", response_model=s.Message)
def open_copy(request: Request, session: SessionDep, document_id: uuid.UUID):
    document = _get(session, document_id)
    if desktop.is_runnable(document.original_filename):
        raise DomainError("Programs and scripts are not opened from the archive.")
    try:
        copy = make_open_copy(
            request.app.state.storage, document, paths.app_home() / "tmp" / "open"
        )
        desktop.open_with_default_app(copy)
    except (DesktopError, OSError) as exc:
        raise DomainError(str(exc)) from exc
    return s.Message(message=N_("Opened a read-only copy."))


@router.post("/documents/{document_id}/reveal", response_model=s.Message)
def reveal(request: Request, session: SessionDep, document_id: uuid.UUID):
    document = _get(session, document_id)
    local_path = getattr(request.app.state.storage, "local_path", None)
    if local_path is None:
        raise DomainError("This archive is not on a local disk.")
    try:
        desktop.show_in_explorer(local_path(document.stored_path))
    except (DesktopError, OSError) as exc:
        raise DomainError(str(exc)) from exc
    return s.Message(message=N_("Shown in Explorer."))
