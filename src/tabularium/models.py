"""Domain model (see docs/requirements.md §4 and docs/adr/0004).

Schema changes go through Alembic migrations only.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
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


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    INSTITUTION = "INSTITUTION"
    OTHER = "OTHER"


class RelationType(StrEnum):
    RELATED = "RELATED"
    PAYMENT_FOR = "PAYMENT_FOR"
    SIGNED_VERSION_OF = "SIGNED_VERSION_OF"
    ATTACHMENT_OF = "ATTACHMENT_OF"
    REPLACES = "REPLACES"


class IngestResult(StrEnum):
    IMPORTED = "IMPORTED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Entity(Base):
    """A person or organization, defined once and referenced by tags."""

    __tablename__ = "entity"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[EntityType] = mapped_column(_str_enum(EntityType, "entity_type"))
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    tags: Mapped[list["Tag"]] = relationship(back_populates="entity")


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
    document_date: Mapped[date | None] = mapped_column(index=True)
    retain_until: Mapped[date] = mapped_column(index=True)
    retain_until_manual: Mapped[bool] = mapped_column(default=False)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    tag_links: Mapped[list["DocumentTag"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


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


class DocumentRelation(Base):
    """Link between documents (e.g. payment confirmation → invoice). UI comes later."""

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
