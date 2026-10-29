"""Import from Evernote: ``.enex`` files in the ingest folder become documents in "To be tagged".

Per note:
- its photos become one PDF, a page per photo, in the note's order;
- every other attachment becomes a document of its own, unchanged;
- a note without attachments is printed to PDF;
- the documents made from one note are linked as related (to the first of them);
- the title becomes the title; notebook, tags and the note's text go into the note field.
  Evernote's dates are not used.

Like the ingest scan, the ``.enex`` file itself is never changed: it moves to ``_processed``
(or ``_failed``). Importing the same note again is recognised by its fingerprint, recorded in
the ingest log, and content that is already archived (or outdated) is not added twice.
"""

import contextlib
import hashlib
import io
import logging
import mimetypes
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import chain
from pathlib import PurePosixPath
from typing import Literal

from sqlalchemy import select

from tabularium.evernote.convert import (
    ConversionError,
    Printer,
    is_photo,
    note_page,
    photos_to_pdf,
)
from tabularium.evernote.enex import Body, Note, Resource, parse_body, read_notes
from tabularium.ingest import (
    EVERNOTE_SUFFIX,
    FAILED,
    PROCESSED,
    Candidate,
    IngestBusyError,
    Ingestor,
)
from tabularium.models import Document, IngestLog, IngestResult, RelationType
from tabularium.services import DomainError
from tabularium.services.documents import create_document, update_details
from tabularium.services.relations import add_link
from tabularium.storage import StagedFile, storage_key

log = logging.getLogger(__name__)

FINGERPRINT = "evernote:"  # ingest log message of every part of an imported note
PDF = "application/pdf"
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

Progress = Callable[[int, int, int], None]  # files done, files total, notes done


@dataclass
class EvernoteReport:
    dry_run: bool
    files: int = 0
    notes: int = 0
    documents: int = 0  # new documents (to be) created
    photo_pdfs: int = 0
    printed: int = 0  # text notes printed to PDF
    attachments: int = 0
    already_imported: int = 0  # notes imported before
    duplicates: int = 0  # parts whose content is archived (or outdated) already
    empty: int = 0  # notes with neither text nor attachments
    failed: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)  # files still being written


@dataclass
class _Part:
    kind: Literal["photos", "attachment", "text"]
    file_name: str
    mime: str
    title: str
    resources: list[Resource] = field(default_factory=list)


def _safe_name(title: str) -> str:
    name = _UNSAFE_NAME.sub(" ", title).strip(" .")
    return re.sub(r"\s+", " ", name)[:120].rstrip(" .") or "note"


def _attachment_name(resource: Resource, index: int) -> str:
    if resource.file_name:
        return _safe_name(resource.file_name)
    ext = mimetypes.guess_extension(resource.mime) or ""
    return f"attachment-{index}{ext}"


def plan_note(note: Note, body: Body) -> list[_Part]:
    """What the note becomes: photos first (one PDF), then each other attachment, or the
    printed note when it has no attachments."""
    order = {md5: i for i, md5 in enumerate(body.media)}
    resources = sorted(note.resources, key=lambda r: order.get(r.md5, len(order)))
    photos = [r for r in resources if is_photo(r.data, r.mime)]
    others = [r for r in resources if r not in photos]
    base = _safe_name(note.title)
    parts: list[_Part] = []
    if photos:
        parts.append(_Part("photos", f"{base}.pdf", PDF, note.title, photos))
    for i, resource in enumerate(others, start=1):
        name = _attachment_name(resource, i)
        parts.append(_Part("attachment", name, resource.mime, note.title, [resource]))
    if not parts and body.text:
        parts.append(_Part("text", f"{base}.pdf", PDF, note.title))
    if len(parts) > 1:  # tell the documents of one note apart
        for part in parts[1:]:
            part.title = f"{note.title} – {part.file_name}" if note.title else part.file_name
    for part in parts:
        part.title = part.title or PurePosixPath(part.file_name).stem
    return parts


def _note_text(notebook: str, note: Note, body: Body, *, with_text: bool) -> str:
    header = f"From Evernote. Notebook: {notebook}."
    if note.tags:
        header += f" Tags: {', '.join(note.tags)}."
    return f"{header}\n\n{body.text}" if with_text and body.text else header


def _with_count(notes: Iterator[Note]) -> tuple[bool, Iterator[Note]]:
    """Whether the file holds more than one note (a whole notebook), without reading it twice."""
    first = next(notes, None)
    second = next(notes, None)
    ahead = [n for n in (first, second) if n is not None]
    return second is not None, chain(ahead, notes)


def notebook_of(relpath: PurePosixPath, whole_notebook: bool) -> str:
    """A notebook exported as a whole is named after it; single notes (evernote-backup
    ``--single-notes``) sit in a folder named after their notebook."""
    if whole_notebook or relpath.parent == PurePosixPath("."):
        return relpath.stem
    return relpath.parent.name


class EvernoteImporter:
    def __init__(self, ingestor: Ingestor, printer: Printer) -> None:
        self.ingestor = ingestor
        self.printer = printer
        self._seen: set[str] = set()  # fingerprints of imported notes, loaded per run

    def files(self) -> list[Candidate]:
        return [c for c in self.ingestor.candidates() if c.path.suffix.lower() == EVERNOTE_SUFFIX]

    def run(self, *, dry_run: bool, progress: Progress | None = None) -> EvernoteReport:
        """Import (or with ``dry_run`` only count) every ready ``.enex`` file.
        Raises IngestBusyError while a scan or another import runs."""
        with self.ingestor.exclusive():
            report = EvernoteReport(dry_run=dry_run)
            self._seen = self._imported_before()
            files = self.files()
            for done, candidate in enumerate(files):
                if progress:
                    progress(done, len(files), report.notes)
                if not self.ingestor.is_stable(candidate):
                    report.waiting.append(str(candidate.relpath))
                    continue
                report.files += 1
                self._file(candidate, report, progress, done, len(files))
            if progress:
                progress(len(files), len(files), report.notes)
            return report

    def _file(
        self, c: Candidate, report: EvernoteReport, progress: Progress | None, done: int, total: int
    ) -> None:
        rel = str(c.relpath)
        failures = len(report.failed)
        run_id = uuid.uuid4()
        try:
            whole_notebook, notes = _with_count(read_notes(c.path))
            notebook = notebook_of(c.relpath, whole_notebook)
            for note in notes:
                report.notes += 1
                self._note(c, notebook, note, report, run_id)
                if progress and report.notes % 10 == 0:
                    progress(done, total, report.notes)
        except Exception as exc:  # noqa: BLE001 — an unreadable file is reported, never lost
            log.exception("Evernote import failed for %s", rel)
            report.failed.append(f"{rel}: {type(exc).__name__}: {exc}")
        if report.dry_run:
            return
        target = PROCESSED if len(report.failed) == failures else FAILED
        try:
            self.ingestor.move_original(c, target)
        except OSError:
            log.warning("Could not move %s to %s", rel, target)

    # --- one note --------------------------------------------------------------------------

    def _imported_before(self) -> set[str]:
        """Fingerprints of the notes imported by earlier runs."""
        with self.ingestor.session_factory() as session:
            messages = session.scalars(
                select(IngestLog.message).where(IngestLog.message.startswith(FINGERPRINT))
            )
            return {m.removeprefix(FINGERPRINT) for m in messages}

    def _note(
        self, c: Candidate, notebook: str, note: Note, report: EvernoteReport, run_id: uuid.UUID
    ) -> None:
        fingerprint = note.fingerprint
        if fingerprint in self._seen:
            report.already_imported += 1
            return
        try:
            if note.problems:
                raise ConversionError("; ".join(note.problems))
            body = parse_body(note.content)
            parts = plan_note(note, body)
            if not parts:
                report.empty += 1
                return
            if report.dry_run:
                self._count(parts, report)
                return
            self._import(c, notebook, note, body, parts, fingerprint, report, run_id)
            self._seen.add(fingerprint)  # the same note in another file of this run
        except Exception as exc:  # noqa: BLE001 — reported per note; the others go on
            log.exception("Evernote import failed for a note in %s", c.relpath)
            report.failed.append(f"{c.relpath}: {note.title or 'untitled note'}: {exc}")
            if not report.dry_run:
                self.ingestor.log_result(
                    run_id, str(c.relpath), IngestResult.FAILED, None, str(exc)
                )

    def _count(self, parts: list[_Part], report: EvernoteReport) -> None:
        for part in parts:
            if part.kind == "attachment":
                sha = hashlib.sha256(part.resources[0].data).hexdigest()
                if self.ingestor.known(sha):
                    report.duplicates += 1
                    continue
            report.documents += 1
            report.photo_pdfs += part.kind == "photos"
            report.printed += part.kind == "text"
            report.attachments += part.kind == "attachment"

    def _content(self, part: _Part, body: Body) -> bytes:
        if part.kind == "photos":
            return photos_to_pdf([r.data for r in part.resources])
        if part.kind == "text":
            return self.printer.print_html(note_page(part.title, body.html))
        return part.resources[0].data

    def _import(
        self,
        c: Candidate,
        notebook: str,
        note: Note,
        body: Body,
        parts: list[_Part],
        fingerprint: str,
        report: EvernoteReport,
        run_id: uuid.UUID,
    ) -> None:
        storage = self.ingestor.storage
        staged: list[tuple[_Part, StagedFile]] = []
        committed: list[str] = []
        try:
            seen: set[str] = set()
            for part in parts:
                try:
                    data = self._content(part, body)
                except ConversionError:
                    raise
                except Exception as exc:
                    raise ConversionError(f"could not convert {part.file_name}: {exc}") from exc
                file = storage.stage(io.BytesIO(data))
                if file.sha256 in seen:  # the same file twice in one note
                    storage.discard(file)
                    continue
                seen.add(file.sha256)
                staged.append((part, file))
            keys: dict[str, str] = {}  # sha256 → archive key of the new files
            for part, file in staged:
                if self.ingestor.known(file.sha256):
                    storage.discard(file)
                    continue
                key = storage_key(file.sha256, PurePosixPath(part.file_name).suffix)
                if storage.commit(file, key):
                    committed.append(key)
                keys[file.sha256] = key
            self._record(c, notebook, note, body, staged, keys, fingerprint, run_id)
            committed = []  # recorded: the files belong to documents now
        except Exception:
            for _part, file in staged:
                storage.discard(file)
            for key in committed:
                storage.remove_uncommitted(key)
            raise
        report.duplicates += len(staged) - len(keys)
        for part, file in staged:
            if file.sha256 in keys:
                report.documents += 1
                report.photo_pdfs += part.kind == "photos"
                report.printed += part.kind == "text"
                report.attachments += part.kind == "attachment"

    def _record(
        self,
        c: Candidate,
        notebook: str,
        note: Note,
        body: Body,
        staged: list[tuple[_Part, StagedFile]],
        keys: dict[str, str],
        fingerprint: str,
        run_id: uuid.UUID,
    ) -> None:
        retention = self.ingestor.config.retention.years
        with self.ingestor.session_factory() as session, session.begin():
            documents: list[Document] = []
            for i, (part, file) in enumerate(staged):
                if file.sha256 in keys:
                    document = create_document(
                        session,
                        sha256=file.sha256,
                        stored_path=keys[file.sha256],
                        original_filename=part.file_name[:500],
                        original_relpath=str(c.relpath),
                        mime_type=part.mime,
                        size_bytes=file.size,
                        retention_years=retention,
                    )
                    update_details(
                        session,
                        document,
                        title=part.title[:500],
                        note=_note_text(
                            notebook, note, body, with_text=i == 0 and part.kind != "text"
                        ),
                    )
                    result = IngestResult.IMPORTED
                else:  # archived before: linked, left as it is
                    document = session.scalar(
                        select(Document).where(Document.sha256 == file.sha256)
                    )
                    result = IngestResult.DUPLICATE
                if document is not None:
                    documents.append(document)
                session.add(
                    IngestLog(
                        run_id=run_id,
                        source_path=f"{c.relpath} → {part.file_name}",
                        sha256=file.sha256,
                        result=result,
                        message=FINGERPRINT + fingerprint,
                    )
                )
            first, *rest = documents or [None]
            for other in rest:
                with contextlib.suppress(DomainError):  # already linked
                    add_link(session, other, first, RelationType.RELATED, "out")


class ImportJob:
    """One import (or preview) at a time, in the background, with its progress."""

    def __init__(self, importer: EvernoteImporter, on_done: Callable[[], None] | None = None):
        self.importer = importer
        self.on_done = on_done
        self._lock = threading.Lock()
        self.running = False
        self.dry_run = False
        self.files_done = self.files_total = self.notes_done = 0
        self.report: EvernoteReport | None = None
        self.error: str | None = None

    def start(self, *, dry_run: bool) -> None:
        with self._lock:
            if self.running:
                raise IngestBusyError("An Evernote import is already running.")
            self.running, self.dry_run = True, dry_run
            self.files_done = self.files_total = self.notes_done = 0
            self.report, self.error = None, None
        threading.Thread(target=self._run, args=(dry_run,), daemon=True).start()

    def _progress(self, files_done: int, files_total: int, notes_done: int) -> None:
        self.files_done, self.files_total, self.notes_done = files_done, files_total, notes_done

    def _run(self, dry_run: bool) -> None:
        try:
            self.report = self.importer.run(dry_run=dry_run, progress=self._progress)
        except IngestBusyError as exc:
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — shown to the user instead of lost
            log.exception("Evernote import failed")
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.running = False
            if self.on_done and not dry_run:
                self.on_done()
