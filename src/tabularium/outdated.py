"""The outdated store: ``outdated.db`` plus the Outdated folder (see docs/adr/0005).

Self-contained on purpose: every outdated document carries a snapshot of its tags (as paths),
the entities behind them, its relations and its change history, so later renames or
deletions in the active archive cannot make it unreadable. A ``metadata.json`` in the
Outdated folder mirrors the database, like the one in the archive.

Schema version 1 is created with ``create_all``; future changes need their own migrations.
"""

import json
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, Text, Uuid, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tabularium import db, metadata
from tabularium.db import UTCDateTime, utcnow
from tabularium.storage import LocalStorage

SCHEMA_VERSION = 1
METADATA_FORMAT = "tabularium-outdated-metadata"


class OutdatedBase(DeclarativeBase):
    pass


class OutdatedDocument(OutdatedBase):
    __tablename__ = "outdated_document"

    # same meaning as in the active archive (models.Document)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    stored_path: Mapped[str] = mapped_column(String(200))  # relative to the Outdated folder
    original_filename: Mapped[str] = mapped_column(String(500))
    original_relpath: Mapped[str | None] = mapped_column(String(2000))
    original_mtime: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(Text)
    document_date: Mapped[date | None]
    retain_until: Mapped[date]
    retain_until_manual: Mapped[bool]
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # outdating
    outdated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    outdated_by: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)  # tags, entities, relations, history

    @property
    def stored_name(self) -> str:
        return PurePosixPath(self.stored_path).name

    @property
    def display_name(self) -> str:
        return self.title or self.stored_name

    @property
    def tag_paths(self) -> list[str]:
        return [t["path"] for t in self.snapshot.get("tags", [])]


class OutdatedEvent(OutdatedBase):
    """History of the outdated store itself (outdated, restored)."""

    __tablename__ = "outdated_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(50))
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class OutdatedSchema(OutdatedBase):
    __tablename__ = "outdated_schema"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class OutdatedStore:
    """``outdated.db`` + the Outdated folder."""

    def __init__(self, database: Path, folder: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.engine = db.make_engine(db.sqlite_url(database))
        OutdatedBase.metadata.create_all(self.engine)
        self.session_factory = db.make_session_factory(self.engine)
        self.folder = folder
        self.storage = LocalStorage(folder)
        with self.session_factory() as session, session.begin():
            if session.get(OutdatedSchema, SCHEMA_VERSION) is None:
                session.add(OutdatedSchema(version=SCHEMA_VERSION))

    def has_hash(self, sha256: str) -> bool:
        with self.session_factory() as session:
            query = select(OutdatedDocument.id).where(OutdatedDocument.sha256 == sha256)
            return session.scalar(query) is not None

    def count(self) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(OutdatedDocument)) or 0

    # --- metadata.json in the Outdated folder -----------------------------------------

    def dump(self) -> dict[str, Any]:
        with self.session_factory() as session:
            tables = {}
            for model in (OutdatedDocument, OutdatedEvent):
                pk = list(model.__table__.primary_key.columns)
                rows = session.scalars(select(model).order_by(*pk)).all()
                tables[model.__tablename__] = [
                    {c.key: metadata._to_json(getattr(r, c.key)) for c in model.__table__.columns}
                    for r in rows
                ]
        for row in tables["outdated_document"]:
            row["_display_name"] = row["title"] or PurePosixPath(row["stored_path"]).name
        return {
            "format": METADATA_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "exported_at": utcnow().isoformat(),
            "counts": {k: len(v) for k, v in tables.items()},
            "tables": tables,
        }

    def write_metadata(self) -> Path:
        return metadata.write(self.folder, self.dump())

    def restore_metadata(self) -> int:
        """Fill an empty outdated.db from the Outdated folder's metadata.json."""
        path = self.folder / metadata.FILENAME
        if not path.exists() or self.count():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != METADATA_FORMAT or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path} is not a supported outdated-store metadata file")
        with self.session_factory() as session, session.begin():
            for model in (OutdatedDocument, OutdatedEvent):
                for row in data["tables"].get(model.__tablename__, []):
                    session.add(
                        model(
                            **{
                                c.key: metadata._from_json(c, row.get(c.key))
                                for c in model.__table__.columns
                            }
                        )
                    )
        return len(data["tables"].get("outdated_document", []))

    def dispose(self) -> None:
        self.engine.dispose()
