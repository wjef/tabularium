"""JSON shapes of the API (``/api/v1``); the TypeScript types are generated from these."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- tags & entities ------------------------------------------------------------------


class TagRef(_Out):
    id: uuid.UUID
    name: str
    path: str
    entity_id: uuid.UUID | None = None


class TagNode(_Out):
    id: uuid.UUID
    name: str
    path: str
    parent_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    color: str | None
    sort_order: int
    count: int  # documents tagged directly
    total: int  # distinct documents tagged with it or any tag below it


class EntityTypeOut(_Out):
    id: uuid.UUID
    name: str
    sort_order: int
    entity_count: int


class EntityOut(_Out):
    id: uuid.UUID
    name: str
    type_id: uuid.UUID
    type_name: str
    aliases: list[str]
    attributes: dict[str, Any]
    tag_count: int


# --- documents --------------------------------------------------------------------------


PreviewKind = Literal["pdf", "image", "text", "none"]
TextState = Literal["PENDING", "DONE", "EMPTY", "NEEDS_OCR", "FAILED"]


class DocRef(_Out):
    id: uuid.UUID
    display_name: str
    document_date: date | None


class DocumentSummary(_Out):
    id: uuid.UUID
    display_name: str
    stored_name: str
    title: str | None
    document_date: date | None
    ingested_at: datetime
    mime_type: str | None
    size_bytes: int
    preview: PreviewKind
    tags: list[TagRef]
    snippet: str | None = None
    text_status: TextState


class DocumentPage(BaseModel):
    total: int
    offset: int
    items: list[DocumentSummary]


class LinkOut(BaseModel):
    relation_id: uuid.UUID
    kind: str  # RelationType value
    direction: Literal["out", "in"]
    phrase: str
    other: DocRef


class TextInfo(BaseModel):
    status: TextState
    method: str | None = None
    pages: int | None = None
    ocr_pages: int | None = None
    chars: int | None = None
    error: str | None = None


class DocumentDetail(DocumentSummary):
    note: str | None
    sha256: str
    stored_path: str
    original_filename: str
    original_relpath: str | None
    retain_until: date
    retain_until_manual: bool
    can_outdate: bool
    links: list[LinkOut]
    text: TextInfo


class HistoryEntry(_Out):
    id: int
    at: datetime
    actor: str
    action: str
    object_type: str
    object_id: str
    old_value: Any | None
    new_value: Any | None
    label: str | None = None  # readable name of the object, if it still exists
    link: str | None = None  # where the object lives in the app


class Message(BaseModel):
    message: str


class LinkChoice(BaseModel):
    value: str  # "TYPE:direction"
    phrase: str


# --- inputs -----------------------------------------------------------------------------


class DocumentUpdate(BaseModel):
    """Only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    note: str | None = None
    document_date: date | None = None
    clear_document_date: bool = False
    retain_until: date | None = None
    retain_auto: bool | None = None


class TagAssign(BaseModel):
    """Assign an existing tag (``tag_id``), a new tag (``name`` under ``parent_id``) or an
    entity tag (``entity_id`` under ``parent_id``, created if missing)."""

    model_config = ConfigDict(extra="forbid")

    tag_id: uuid.UUID | None = None
    name: str | None = None
    parent_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None


class BulkTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID]
    tag_id: uuid.UUID


class LinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    other_id: uuid.UUID
    kind: str  # "TYPE:direction" as offered by /link-choices


class OutdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
