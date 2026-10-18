"""Ingest pipeline (see docs/requirements.md §3 and docs/adr/0003).

Per file, in crash-safe order: stage (copy + hash) → verify the source did not change →
commit to the archive → record in the database → move the original to ``_processed``.
Nothing is ever lost: if a step fails the original goes to ``_failed`` (or stays put),
duplicates go to ``_duplicates``.
"""

import fnmatch
import logging
import mimetypes
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tabularium.config import Config
from tabularium.fsutil import (
    is_hidden_or_system,
    is_locked,
    is_placeholder,
    lp,
    move_file,
    strip_lp,
)
from tabularium.models import Document, IngestLog, IngestResult
from tabularium.services.documents import create_document
from tabularium.storage import Storage, storage_key

log = logging.getLogger(__name__)

PROCESSED, DUPLICATES, FAILED = "_processed", "_duplicates", "_failed"
RESERVED_FOLDERS = frozenset({PROCESSED, DUPLICATES, FAILED})
STALE_STAGING_SECONDS = 24 * 3600
BUILTIN_IGNORE = ("~$*", ".~*", "*.tmp", "*.partial", "*.crdownload", "desktop.ini", "thumbs.db")


class IngestBusyError(Exception):
    """A scan is already running."""


@dataclass
class IngestReport:
    run_id: uuid.UUID
    imported: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # e.g. cloud-only placeholders
    waiting: list[str] = field(default_factory=list)  # still being written / locked

    @property
    def total(self) -> int:
        return sum(len(x) for x in (self.imported, self.duplicates, self.failed, self.skipped))


@dataclass(frozen=True)
class _Candidate:
    path: Path  # long-path form on Windows
    relpath: PurePosixPath  # relative to the ingest root, for logs and the database
    stat: os.stat_result


def _signature(st: os.stat_result) -> tuple[int, int]:
    return st.st_size, st.st_mtime_ns


class Ingestor:
    def __init__(
        self,
        config: Config,
        session_factory: sessionmaker[Session],
        storage: Storage,
        *,
        clock: Callable[[], float] = time.time,
        is_outdated: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.storage = storage
        self.clock = clock
        self.is_outdated = is_outdated  # hash known in the outdated store?
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self.config.paths.ingest

    def scan(self) -> IngestReport:
        """Process every ready file in the ingest folder. Raises IngestBusyError if running."""
        if not self._lock.acquire(blocking=False):
            raise IngestBusyError("An ingest scan is already running.")
        try:
            report = IngestReport(run_id=uuid.uuid4())
            # Copies staged by a run that was interrupted (app closed mid-copy).
            self.storage.cleanup_staging(older_than=STALE_STAGING_SECONDS, now=self.clock())
            for candidate in self._candidates():
                self._process(candidate, report)
            if report.total or report.waiting:
                log.info(
                    "Ingest %s: %d imported, %d duplicates, %d failed, %d skipped, %d waiting",
                    report.run_id,
                    len(report.imported),
                    len(report.duplicates),
                    len(report.failed),
                    len(report.skipped),
                    len(report.waiting),
                )
            return report
        finally:
            self._lock.release()

    # --- discovery -------------------------------------------------------------------

    def _ignored(self, name: str) -> bool:
        lowered = name.lower()
        patterns = (*BUILTIN_IGNORE, *self.config.ingest.ignore)
        return lowered.startswith(".") or any(fnmatch.fnmatch(lowered, p.lower()) for p in patterns)

    def _candidates(self) -> Iterator[_Candidate]:
        root = lp(self.root)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = PurePosixPath(*Path(os.path.relpath(dirpath, root)).parts)
            if rel_dir == PurePosixPath("."):
                dirnames[:] = [d for d in dirnames if d not in RESERVED_FOLDERS]
            if not self.config.ingest.include_subfolders:
                dirnames[:] = []
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if self._ignored(name):
                    continue
                path = Path(dirpath) / name
                try:
                    st = os.stat(path)
                except OSError:
                    continue  # vanished meanwhile
                if is_hidden_or_system(st):
                    continue
                relpath = rel_dir / name if rel_dir != PurePosixPath(".") else PurePosixPath(name)
                yield _Candidate(path, relpath, st)

    def _is_stable(self, candidate: _Candidate) -> bool:
        st, now = candidate.stat, self.clock()
        # Birth time matters: sync clients and copies may preserve an old modification time.
        # Timestamps in the future (preserved from elsewhere, clock rounding) are ignored,
        # otherwise such a file would wait forever.
        stamps = (st.st_mtime, getattr(st, "st_birthtime", None))
        newest = max((t for t in stamps if t is not None and t <= now), default=None)
        if newest is not None and now - newest < self.config.ingest.stable_seconds:
            return False
        return not is_locked(candidate.path)

    # --- processing ------------------------------------------------------------------

    def _process(self, c: _Candidate, report: IngestReport) -> None:
        rel = str(c.relpath)
        if is_placeholder(c.stat):
            self._log(report, c, IngestResult.SKIPPED, None, "cloud-only placeholder")
            report.skipped.append(rel)
            return
        if not self._is_stable(c):
            report.waiting.append(rel)
            return

        staged = None
        committed_key = None
        try:
            with open(c.path, "rb") as source:
                staged = self.storage.stage(source)
            current = os.stat(c.path)
            if _signature(current) != _signature(c.stat) or staged.size != current.st_size:
                self.storage.discard(staged)
                report.waiting.append(rel)  # changed while copying: try again next scan
                return

            with self.session_factory() as session:
                exists = session.scalar(select(Document.id).where(Document.sha256 == staged.sha256))
            if exists is None and self.is_outdated is not None:
                exists = self.is_outdated(staged.sha256) or None
            if exists is not None:
                self.storage.discard(staged)
                self._move_original(c, DUPLICATES)
                self._log(report, c, IngestResult.DUPLICATE, staged.sha256, None)
                report.duplicates.append(rel)
                return

            key = storage_key(staged.sha256, c.path.suffix)
            if self.storage.commit(staged, key):
                committed_key = key
            self._record(c, staged.sha256, staged.size, key, report.run_id)
            committed_key = None  # recorded: the archive file now belongs to a document
            report.imported.append(rel)
        except Exception as exc:  # noqa: BLE001 — every failure is reported, never lost
            log.exception("Ingest failed for %s", rel)
            if staged is not None:
                self.storage.discard(staged)
            if committed_key is not None:
                self.storage.remove_uncommitted(committed_key)
            self._fail(c, report, exc)
            return

        try:
            self._move_original(c, PROCESSED)
        except OSError:
            # Archived and recorded; the original stays and will be seen as a duplicate later.
            log.warning("Imported %s but could not move the original to %s", rel, PROCESSED)

    def _record(self, c: _Candidate, sha256: str, size: int, key: str, run_id: uuid.UUID) -> None:
        name = c.relpath.name
        parent = str(c.relpath.parent)
        with self.session_factory() as session, session.begin():
            create_document(
                session,
                sha256=sha256,
                stored_path=key,
                original_filename=name,
                original_relpath=None if parent == "." else parent,
                original_mtime=datetime.fromtimestamp(c.stat.st_mtime, UTC),
                mime_type=mimetypes.guess_type(name)[0],
                size_bytes=size,
                retention_years=self.config.retention.years,
            )
            session.add(
                IngestLog(
                    run_id=run_id,
                    source_path=str(c.relpath),
                    sha256=sha256,
                    result=IngestResult.IMPORTED,
                )
            )

    def _move_original(self, c: _Candidate, reserved: str) -> Path:
        target_dir = self.root / reserved / Path(*c.relpath.parent.parts)
        return Path(strip_lp(str(move_file(c.path, target_dir))))

    def _fail(self, c: _Candidate, report: IngestReport, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        try:
            self._move_original(c, FAILED)
        except OSError as move_exc:
            message += f" (original left in place: {move_exc})"
        self._log(report, c, IngestResult.FAILED, None, message)
        report.failed.append(str(c.relpath))

    def _log(
        self,
        report: IngestReport,
        c: _Candidate,
        result: IngestResult,
        sha256: str | None,
        message: str | None,
    ) -> None:
        try:
            with self.session_factory() as session, session.begin():
                session.add(
                    IngestLog(
                        run_id=report.run_id,
                        source_path=str(c.relpath),
                        sha256=sha256,
                        result=result,
                        message=message,
                    )
                )
        except Exception:  # noqa: BLE001 — logging must not break the scan
            log.exception("Could not write ingest log for %s", c.relpath)
