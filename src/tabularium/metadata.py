"""``metadata.json`` in the archive folder: a complete, human-readable copy of the database.

Together with the hash-named files it allows rebuilding the database on a new PC or after a
loss (see docs/requirements.md §10). The file is rewritten atomically shortly after changes.

Rows are serialized generically from the models, so new columns are included automatically.
Keys starting with ``_`` are readable extras (tag paths…) and are ignored when restoring.
"""

import json
import logging
import os
import threading
import uuid
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Date, Uuid, delete, event, func, select
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Session, sessionmaker

from tabularium import __version__
from tabularium.db import Base, UTCDateTime, utcnow
from tabularium.models import (
    AuditLog,
    Document,
    DocumentRelation,
    DocumentTag,
    Entity,
    EntityMembership,
    EntityType,
    IngestLog,
    Tag,
)
from tabularium.services import DomainError
from tabularium.services.tags import tag_paths

log = logging.getLogger(__name__)

FILENAME = "metadata.json"
FORMAT = "tabularium-metadata"
FORMAT_VERSION = 1
# insert order respects foreign keys (tags are additionally sorted parents-first)
MODELS: list[type[Base]] = [
    EntityType,
    Entity,
    EntityMembership,
    Tag,
    Document,
    DocumentTag,
    DocumentRelation,
    AuditLog,
]


# --- serialization ----------------------------------------------------------------------


def _to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _from_json(column, value: Any) -> Any:
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, Uuid):
        return uuid.UUID(value)
    if isinstance(kind, UTCDateTime):
        return datetime.fromisoformat(value)
    if isinstance(kind, Date):
        return date.fromisoformat(value)
    if isinstance(kind, SAEnum) and kind.enum_class is not None:
        return kind.enum_class(value)
    return value


def _columns(model: type[Base]):
    return list(model.__table__.columns)


def _row(obj: Base) -> dict[str, Any]:
    return {c.key: _to_json(getattr(obj, c.key)) for c in _columns(type(obj))}


def dump(session: Session, schema_revision: str | None) -> dict[str, Any]:
    paths = tag_paths(session)
    tables: dict[str, list[dict]] = {}
    for model in MODELS:
        pk = [c.key for c in model.__table__.primary_key.columns]
        rows = session.scalars(select(model).order_by(*(getattr(model, k) for k in pk))).all()
        tables[model.__tablename__] = [_row(r) for r in rows]
    for tag in tables["tag"]:
        tag["_path"] = paths[uuid.UUID(tag["id"])]
    by_doc: dict[str, list[str]] = {}
    for link in tables["document_tag"]:
        by_doc.setdefault(link["document_id"], []).append(paths[uuid.UUID(link["tag_id"])])
    for document in tables["document"]:
        document["_tags"] = sorted(by_doc.get(document["id"], []), key=str.casefold)
        document["_display_name"] = document["title"] or Path(document["stored_path"]).name
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "schema_revision": schema_revision,
        "exported_at": utcnow().isoformat(),
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }


def write(archive_root: Path, data: dict[str, Any]) -> Path:
    """Atomic write: readers (and OneDrive) never see a half-written file."""
    target = archive_root / FILENAME
    temp = archive_root / f"{FILENAME}.tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, target)
    return target


def read(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        raise DomainError(f"{path.name} is not a Tabularium metadata file.")
    if data.get("format_version") != FORMAT_VERSION:
        raise DomainError(f"Unsupported metadata format version {data.get('format_version')}.")
    return data


# --- restore ----------------------------------------------------------------------------


def is_empty(session: Session) -> bool:
    """No user data yet (the starting entity types and logs do not count)."""
    for model in (Document, Tag, Entity, EntityMembership, DocumentRelation):
        if session.scalar(select(func.count()).select_from(model)):
            return False
    return True


def _tags_parents_first(rows: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in rows}

    def depth(row) -> int:
        d = 0
        while row["parent_id"] is not None:
            row, d = by_id[row["parent_id"]], d + 1
        return d

    return sorted(rows, key=depth)


def restore(session: Session, data: dict[str, Any], schema_revision: str | None) -> dict[str, int]:
    """Fill an empty database from metadata. The caller commits."""
    if data.get("schema_revision") != schema_revision:
        raise DomainError(
            f"The metadata was written by database version {data.get('schema_revision')}, "
            f"this app uses {schema_revision}. Use the matching app version to restore."
        )
    if not is_empty(session):
        raise DomainError(
            "Restore only works into an empty database (no documents, tags, entities)."
        )
    # starting rows created by migrations / startup are replaced by the archived ones
    for model in (AuditLog, IngestLog, EntityType):
        session.execute(delete(model))

    counts = {}
    for model in MODELS:
        rows = data["tables"].get(model.__tablename__, [])
        if model is Tag:
            rows = _tags_parents_first(rows)
        columns = _columns(model)
        for row in rows:
            session.add(model(**{c.key: _from_json(c, row.get(c.key)) for c in columns}))
        session.flush()
        counts[model.__tablename__] = len(rows)
    session.add(
        AuditLog(
            actor="tabularium",
            action="RESTORE",
            object_type="database",
            object_id=FILENAME,
            new_value={"exported_at": data.get("exported_at"), "counts": counts},
        )
    )
    session.flush()
    return counts


# --- automatic export ---------------------------------------------------------------------


class MetadataExporter:
    """Rewrites ``metadata.json`` shortly after any committed change (and on demand)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        archive_root: Path,
        schema_revision,
        interval: float = 30,
    ) -> None:
        self.session_factory = session_factory
        self.archive_root = archive_root
        self.schema_revision = schema_revision  # callable returning the current revision
        self.interval = interval
        self.dirty = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_written: datetime | None = None
        self.last_error: str | None = None

    def attach(self) -> "MetadataExporter":
        """Mark dirty whenever a session commits changes."""

        @event.listens_for(self.session_factory, "after_flush")
        def _changed(session, _flush_context):
            session.info["tabularium_changed"] = True

        @event.listens_for(self.session_factory, "after_commit")
        def _committed(session):
            if session.info.pop("tabularium_changed", False):
                self.dirty.set()

        @event.listens_for(self.session_factory, "after_rollback")
        def _rolled_back(session):
            session.info.pop("tabularium_changed", None)

        return self

    def write_now(self) -> Path:
        with self._lock:
            self.dirty.clear()
            try:
                with self.session_factory() as session:
                    data = dump(session, self.schema_revision())
                path = write(self.archive_root, data)
            except Exception as exc:
                self.dirty.set()  # try again later (e.g. file briefly locked by OneDrive)
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise
            self.last_written, self.last_error = utcnow(), None
            return path

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            if self.dirty.is_set():
                try:
                    self.write_now()
                except Exception:  # noqa: BLE001 — retried next interval
                    log.exception("Could not write %s", FILENAME)

    def start(self) -> None:
        self.dirty.set()  # always refresh once after start
        self._thread = threading.Thread(target=self._loop, name="metadata", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the loop and write pending changes."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self.dirty.is_set():
            try:
                self.write_now()
            except Exception:  # noqa: BLE001
                log.exception("Could not write %s on shutdown", FILENAME)
