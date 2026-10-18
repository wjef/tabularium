"""Full-text index: keeps ``document_text`` and the FTS5 table in sync; background indexer.

The FTS table stores normalized text (``tabularium.text.normalize``), so searching ignores
case and Polish diacritics exactly like the rest of the app.
"""

import logging
import os
import threading
import unicodedata
import uuid
from collections.abc import Callable

from sqlalchemy import column, func, literal_column, or_, select, table, update
from sqlalchemy.orm import Session, sessionmaker

from tabularium.db import FTS_TABLE, utcnow
from tabularium.fsutil import is_placeholder
from tabularium.models import Document, DocumentText, TextStatus
from tabularium.storage import Storage
from tabularium.text import normalize
from tabularium.textextract import Extraction, OcrEngine, extract

log = logging.getLogger(__name__)
_fts = table(FTS_TABLE, column("document_id"), column("body"))


# --- index maintenance -------------------------------------------------------------------


def store_extraction(session: Session, document_id: uuid.UUID, result: Extraction) -> None:
    row = session.get(DocumentText, document_id) or DocumentText(document_id=document_id)
    row.status = result.status
    row.method = result.method
    row.pages = result.pages
    row.ocr_pages = result.ocr_pages
    row.content = result.text or None
    row.chars = len(result.text) if result.text else 0
    row.error = result.error
    row.extracted_at = utcnow()
    session.add(row)
    session.execute(_fts.delete().where(_fts.c.document_id == document_id.hex))
    if result.text:
        session.execute(
            _fts.insert().values(document_id=document_id.hex, body=normalize(result.text))
        )


def remove_from_index(session: Session, document_id: uuid.UUID) -> None:
    """Drop the searchable text (the document_text row goes with the document)."""
    session.execute(_fts.delete().where(_fts.c.document_id == document_id.hex))


def request_extraction(session: Session, document_id: uuid.UUID) -> None:
    """Queue a document for (re-)extraction ("Extract again")."""
    row = session.get(DocumentText, document_id)
    if row is not None:
        row.status = TextStatus.PENDING
        row.error = None


def requeue_waiting_for_ocr(session: Session) -> int:
    result = session.execute(
        update(DocumentText)
        .where(DocumentText.status == TextStatus.NEEDS_OCR)
        .values(status=TextStatus.PENDING)
    )
    return result.rowcount or 0


def matching_ids(word: str):
    """Subquery: ids of documents whose text contains ``word`` (a word prefix, normalized)."""
    phrase = '"' + normalize(word).replace('"', '""') + '"*'
    # FTS5: "<table> MATCH <query>"; the bound value keeps quoting safe. Ids are stored as
    # 32 hex characters, which is how SQLAlchemy's Uuid type stores them on SQLite.
    return select(_fts.c.document_id).where(literal_column(FTS_TABLE).op("MATCH")(phrase))


def status_counts(session: Session) -> dict[str, int]:
    counts = dict(
        session.execute(
            select(DocumentText.status, func.count()).group_by(DocumentText.status)
        ).all()
    )
    total = session.scalar(select(func.count()).select_from(Document)) or 0
    result = {s.value: counts.get(s, 0) for s in TextStatus}
    result[TextStatus.PENDING.value] += total - sum(counts.values())  # never processed yet
    return result


# --- snippets ---------------------------------------------------------------------------


def _fold(ch: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", normalize(ch) if ch.strip() else " ")
        if not unicodedata.combining(c)
    )


def snippet(content: str, words: list[str], width: int = 160) -> str | None:
    """A piece of the original text around the first matching word (Polish letters kept)."""
    if not content or not words:
        return None
    folded, index = [], []
    for i, ch in enumerate(content):
        f = _fold(ch)
        folded.append(f)
        index.extend([i] * len(f))
    haystack = "".join(folded)
    for word in words:
        pos = haystack.find(normalize(word))
        if pos >= 0:
            start = max(0, index[pos] - width // 3)
            end = min(len(content), start + width)
            piece = " ".join(content[start:end].split())
            return ("…" if start else "") + piece + ("…" if end < len(content) else "")
    return None


# --- background indexer -----------------------------------------------------------------


class TextIndexer:
    """Extracts text for documents without it, one at a time, in a background thread."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: Storage,
        *,
        ocr_factory: Callable[[], OcrEngine | None],
        dpi: int = 300,
        max_pages: int = 300,
        interval: float = 5,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.ocr_factory = ocr_factory
        self.dpi, self.max_pages, self.interval = dpi, max_pages, interval
        self.ocr: OcrEngine | None = None
        self._skip: set[uuid.UUID] = set()  # e.g. cloud-only files, retried after restart
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.refresh_ocr()

    def refresh_ocr(self) -> bool:
        """(Re)detect OCR; documents waiting for it are queued again when it appears."""
        had = self.ocr is not None
        self.ocr = self.ocr_factory()
        if self.ocr is not None and not had:
            with self.session_factory() as session, session.begin():
                requeued = requeue_waiting_for_ocr(session)
            if requeued:
                log.info("OCR available: %d document(s) queued for OCR", requeued)
        self._wake.set()
        return self.ocr is not None

    def _next(self, session: Session) -> Document | None:
        query = (
            select(Document)
            .outerjoin(DocumentText, DocumentText.document_id == Document.id)
            .where(
                or_(DocumentText.document_id.is_(None), DocumentText.status == TextStatus.PENDING)
            )
            .order_by(Document.ingested_at)
        )
        if self._skip:
            query = query.where(Document.id.not_in(self._skip))
        return session.scalars(query.limit(1)).first()

    def process_next(self) -> bool:
        """Extract one document. Returns False when nothing is waiting."""
        with self.session_factory() as session:
            document = self._next(session)
            if document is None:
                return False
            doc_id, key, mime = document.id, document.stored_path, document.mime_type
        local_path = getattr(self.storage, "local_path", None)
        if local_path is not None:
            try:
                if is_placeholder(os.stat(local_path(key))):
                    self._skip.add(doc_id)  # cloud-only: reading would download it
                    return True
            except OSError:
                pass
        if not self.storage.exists(key):
            result = Extraction(TextStatus.FAILED, error="file missing from the archive")
        else:
            with self.storage.open(key) as stream:
                result = extract(stream, mime, ocr=self.ocr, dpi=self.dpi, max_pages=self.max_pages)
        with self.session_factory() as session, session.begin():
            if session.get(Document, doc_id) is not None:  # not outdated meanwhile
                store_extraction(session, doc_id, result)
        return True

    def run_until_idle(self, limit: int | None = None) -> int:
        done = 0
        while (limit is None or done < limit) and not self._stop.is_set() and self.process_next():
            done += 1
        return done

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_until_idle()
            except Exception:  # noqa: BLE001 — keep indexing other documents later
                log.exception("Text indexing failed")
            self._wake.wait(self.interval)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="text-index", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
