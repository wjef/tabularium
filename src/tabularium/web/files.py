"""Serving archived files by document ID (never by path; see docs/adr/0007).

Only types that cannot run script in our origin are shown inline. Everything else — HTML,
SVG, XML, Office files… — is sent as a download, so an archived file can never act as part
of the app (it would otherwise be able to read the CSRF token).
"""

from collections.abc import Iterator
from urllib.parse import quote

from fastapi.responses import StreamingResponse

from tabularium.export import readable_name
from tabularium.models import Document
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
