"""Archive integrity check (see docs/requirements.md §10). Reports only — never changes files.

- missing:      a document's file is not in the archive
- changed:      size differs (quick) or content hash differs (full)
- cloud_only:   OneDrive placeholder; not read (that would download it), so not verified
- orphans:      files in the archive that no document refers to (e.g. OneDrive conflict
                copies, leftovers); ``matches`` names a missing document with the same content
"""

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.fsutil import is_hidden_or_system, is_placeholder, lp
from tabularium.metadata import FILENAME as METADATA_FILENAME
from tabularium.models import Document
from tabularium.storage import LocalStorage, sha256_of

# archive-root entries that belong to the app, not to documents
_APP_FILES = {METADATA_FILENAME, f"{METADATA_FILENAME}.tmp"}
_APP_DIRS = {LocalStorage.STAGING}


@dataclass
class Problem:
    path: str  # archive-relative, forward slashes
    document_id: str | None = None
    name: str | None = None  # display name of the document
    detail: str = ""


@dataclass
class IntegrityReport:
    full: bool
    checked: int = 0
    ok: int = 0
    missing: list[Problem] = field(default_factory=list)
    changed: list[Problem] = field(default_factory=list)
    cloud_only: list[Problem] = field(default_factory=list)
    orphans: list[Problem] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not (self.missing or self.changed or self.orphans)


def _archive_files(root: Path) -> dict[str, Path]:
    """All files below ``root`` that could be documents: relative path → full path."""
    found: dict[str, Path] = {}
    base = lp(root)
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = PurePosixPath(*Path(os.path.relpath(dirpath, base)).parts)
        if rel_dir == PurePosixPath("."):
            dirnames[:] = [d for d in dirnames if d not in _APP_DIRS]
            filenames = [f for f in filenames if f not in _APP_FILES]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                if is_hidden_or_system(os.stat(full)):
                    continue  # desktop.ini and the like
            except OSError:
                continue
            rel = str(rel_dir / name) if rel_dir != PurePosixPath(".") else name
            found[rel] = full
    return found


def check(session: Session, archive_root: Path, *, full: bool = False) -> IntegrityReport:
    """Compare documents with the archive folder. ``full`` re-hashes every local file."""
    report = IntegrityReport(full=full)
    files = _archive_files(archive_root)
    documents = session.scalars(select(Document).order_by(Document.stored_path)).all()
    missing_by_hash: dict[str, Problem] = {}

    for doc in documents:
        report.checked += 1
        problem = Problem(doc.stored_path, str(doc.id), doc.display_name)
        path = files.pop(doc.stored_path, None)
        if path is None:
            report.missing.append(problem)
            missing_by_hash[doc.sha256] = problem
            continue
        st = os.stat(path)
        if is_placeholder(st):
            report.cloud_only.append(problem)
            continue
        if st.st_size != doc.size_bytes:
            problem.detail = f"size {st.st_size} B, expected {doc.size_bytes} B"
            report.changed.append(problem)
            continue
        if full:
            with open(path, "rb") as f:
                actual = sha256_of(f)
            if actual != doc.sha256:
                problem.detail = f"content hash {actual[:12]}…, expected {doc.sha256[:12]}…"
                report.changed.append(problem)
                continue
        report.ok += 1

    for rel, path in sorted(files.items()):
        orphan = Problem(rel)
        st = os.stat(path)
        if not is_placeholder(st) and missing_by_hash:
            with open(path, "rb") as f:
                match = missing_by_hash.get(sha256_of(f))
            if match is not None:
                orphan.detail = f"same content as missing {match.path}"
                orphan.document_id, orphan.name = match.document_id, match.name
        report.orphans.append(orphan)
    return report
