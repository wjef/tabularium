"""Serving archived files by document ID (never by path; see docs/adr/0007).

Only types that cannot run script in our origin are shown inline. Everything else — HTML,
SVG, XML, Office files… — is sent as a download, so an archived file can never act as part
of the app (it would otherwise be able to read the CSRF token).
"""

import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.export import readable_name, write_zip
from tabularium.models import Document, DocumentTag
from tabularium.services.tags import tag_paths
from tabularium.storage import CHUNK, Storage

INLINE_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "text/plain",
    }
)


def preview_kind(document: Document) -> str:
    """How the UI previews a document: ``pdf``, ``image``, ``text`` or ``none``."""
    mime = document.mime_type or ""
    if mime not in INLINE_TYPES:
        return "none"
    if mime == "application/pdf":
        return "pdf"
    return "image" if mime.startswith("image/") else "text"


def _content_disposition(kind: str, filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def file_response(storage: Storage, document: Document, *, download: bool) -> StreamingResponse:
    mime = document.mime_type or "application/octet-stream"
    inline = not download and mime in INLINE_TYPES
    if not inline:
        mime = mime if mime in INLINE_TYPES else "application/octet-stream"
    media_type = f"{mime}; charset=utf-8" if mime == "text/plain" else mime

    def chunks() -> Iterator[bytes]:
        with storage.open(document.stored_path) as stream:
            while chunk := stream.read(CHUNK):
                yield chunk

    headers = {
        "Content-Disposition": _content_disposition(
            "inline" if inline else "attachment", readable_name(document)
        ),
        "Cache-Control": "private, max-age=3600",
    }
    return StreamingResponse(chunks(), media_type=media_type, headers=headers)


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


def export_zip(request: Request, session: Session, ids: list[uuid.UUID]) -> StreamingResponse:
    """Zip of readable copies of ``ids`` plus index.csv (see tabularium.export)."""
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
    missing = [d.display_name for d in documents if not storage.exists(d.stored_path)]
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
