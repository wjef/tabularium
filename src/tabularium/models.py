"""Domain model (see docs/requirements.md §4 and docs/adr/0004).

Schema changes go through Alembic migrations only.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tabularium.db import Base, UTCDateTime, utcnow


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Stored as plain strings: adding a member needs no table rebuild."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


class RelationType(StrEnum):
    RELATED = "RELATED"
    PAYMENT_FOR = "PAYMENT_FOR"
    SIGNED_VERSION_OF = "SIGNED_VERSION_OF"
    ATTACHMENT_OF = "ATTACHMENT_OF"
    REPLACES = "REPLACES"
    CORRECTION_OF = "CORRECTION_OF"  # e.g. a correction invoice → the original invoice


class TextStatus(StrEnum):
    PENDING = "PENDING"  # waiting for the background indexer
    DONE = "DONE"
    EMPTY = "EMPTY"  # no text found / file type without text
    NEEDS_OCR = "NEEDS_OCR"  # scanned pages, but OCR is not available yet
    FAILED = "FAILED"


class IngestResult(StrEnum):
    IMPORTED = "IMPORTED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EntityType(Base):
    """User-defined kind of entity (Person, Company, Bank, School…)."""

    __tablename__ = "entity_type"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    entities: Mapped[list["Entity"]] = relationship(back_populates="type")


class Entity(Base):
    """A person or organization, defined once and referenced by tags."""

    __tablename__ = "entity"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_type.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    type: Mapped[EntityType] = relationship(back_populates="entities")
    tags: Mapped[list["Tag"]] = relationship(back_populates="entity")


class EntityMembership(Base):
    """``member`` belongs to ``group`` (Jan Kowalski → Kowalski Family). Many-to-many, nestable."""

    __tablename__ = "entity_membership"
    __table_args__ = (CheckConstraint("member_id <> group_id", name="not_self"),)

    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Tag(Base):
    """Node of the tag tree. Root tags act as facets. May point to an entity."""

    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("parent_id", "normalized_name", name="uq_tag_parent_name"),
        # NULLs are distinct in unique constraints, so root names need their own index.
        Index(
            "uq_tag_root_name",
            "normalized_name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tag.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entity.id", ondelete="RESTRICT"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    parent: Mapped["Tag | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Tag"]] = relationship(back_populates="parent")
    entity: Mapped[Entity | None] = relationship(back_populates="tags")


class Document(Base):
    """An archived file. The file itself lives in the archive under ``stored_path``."""

    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    stored_path: Mapped[str] = mapped_column(String(200))  # relative to the archive root
    original_filename: Mapped[str] = mapped_column(String(500))
    original_relpath: Mapped[str | None] = mapped_column(String(2000))
    original_mtime: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(Text)
    document_date: Mapped[date | None] = mapped_column(index=True)  # date created (the main date)
    sent_date: Mapped[date | None]  # supporting dates, shown in the details
    received_date: Mapped[date | None]
    retain_until: Mapped[date] = mapped_column(index=True)
    retain_until_manual: Mapped[bool] = mapped_column(default=False)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # When the user finished the initial tagging; until then the document is "To be tagged".
    tagging_done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    # When the user moved it to Trash: hidden everywhere except the Trash section, never deleted.
    trashed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    tag_links: Mapped[list["DocumentTag"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def stored_name(self) -> str:
        """File name in the archive: ``<sha256>.<ext>``."""
        return PurePosixPath(self.stored_path).name

    @property
    def display_name(self) -> str:
        """How the app names the document: its title, otherwise its archive file name.

        Original file names are often random (scanner/phone names), so they are only shown as
        reference information, never as the document's name.
        """
        return self.title or self.stored_name


class DocumentTag(Base):
    __tablename__ = "document_tag"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), primary_key=True
    )
    # RESTRICT: a tag in use cannot disappear; it must be merged or reassigned first.
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tag.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    tagged_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship()


class DocumentText(Base):
    """Text extracted from a document (text layer and/or OCR). Regenerable, so not exported.

    The searchable copy lives in the FTS5 table ``document_fts`` (normalized text), kept in
    sync by ``tabularium.textindex``.
    """

    __tablename__ = "document_text"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[TextStatus] = mapped_column(_str_enum(TextStatus, "text_status"), index=True)
    method: Mapped[str | None] = mapped_column(String(20))  # text | ocr | mixed
    pages: Mapped[int | None] = mapped_column(Integer)
    ocr_pages: Mapped[int | None] = mapped_column(Integer)
    chars: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DocumentRelation(Base):
    """Directed link: ``from`` <type> ``to`` (payment confirmation PAYMENT_FOR invoice)."""

    __tablename__ = "document_relation"
    __table_args__ = (
        UniqueConstraint("from_document_id", "to_document_id", "type", name="uq_relation"),
        CheckConstraint("from_document_id <> to_document_id", name="not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    to_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[RelationType] = mapped_column(_str_enum(RelationType, "relation_type"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditLog(Base):
    """Append-only change history. No foreign keys: entries outlive their objects."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_object", "object_type", "object_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(50))
    object_type: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[Any | None] = mapped_column(JSON)
    new_value: Mapped[Any | None] = mapped_column(JSON)


class IngestLog(Base):
    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    source_path: Mapped[str] = mapped_column(String(2000))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    result: Mapped[IngestResult] = mapped_column(_str_enum(IngestResult, "ingest_result"))
    message: Mapped[str | None] = mapped_column(Text)
