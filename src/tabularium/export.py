"""Readable copies of archived documents: zip export and "open a copy" (docs/requirements.md §8).

Archived files keep their hash names; anything handed to the user gets a readable name such as
``2025-04-01 Zeznanie PIT 2024.pdf`` (title), or the hash when there is no title. The archive
itself is never touched.
"""

import csv
import io
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePath
from typing import BinaryIO

from tabularium.fsutil import lp
from tabularium.models import Document
from tabularium.storage import Storage

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.IGNORECASE)
MAX_STEM = 120


def readable_name(document: Document) -> str:
    """``[YYYY-MM-DD ]<title or sha256>.<ext>``, safe as a Windows file name.

    Used for everything handed out (downloads, previews, exports, opened copies). The original
    file name is never used (it is often random); it stays in the database for tracking and is
    listed in ``index.csv``. The extension comes from the archive file name.
    """
    suffix = PurePath(document.stored_path).suffix or PurePath(document.original_filename).suffix
    stem = document.title or document.sha256
    if document.document_date:
        stem = f"{document.document_date.isoformat()} {stem}"
    stem = _INVALID.sub("_", stem).strip().rstrip(".") or "document"
    if _RESERVED.match(stem):
        stem = f"_{stem}"
    return stem[:MAX_STEM].rstrip(" .") + suffix.lower()


def unique_names(documents: Iterable[Document]) -> list[tuple[Document, str]]:
    """Readable names, made unique case-insensitively with `` (2)``, `` (3)``…"""
    used: set[str] = set()
    result = []
    for document in documents:
        name = readable_name(document)
        stem, suffix = PurePath(name).stem, PurePath(name).suffix
        candidate, n = name, 2
        while candidate.lower() in used:
            candidate = f"{stem} ({n}){suffix}"
            n += 1
        used.add(candidate.lower())
        result.append((document, candidate))
    return result


def write_zip(
    storage: Storage,
    documents: list[Document],
    tag_paths: dict[uuid.UUID, list[str]],
    out: BinaryIO,
) -> None:
    """Zip of readable copies plus ``index.csv`` (name, original name, date, tags, sha256)."""
    index = io.StringIO()
    writer = csv.writer(index, delimiter=";")
    writer.writerow(["file", "original_filename", "document_date", "title", "tags", "sha256"])
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for document, name in unique_names(documents):
            large = document.size_bytes >= zipfile.ZIP64_LIMIT  # files over 2 GiB
            with (
                storage.open(document.stored_path) as src,
                archive.open(name, "w", force_zip64=large) as dst,
            ):
                shutil.copyfileobj(src, dst, 1024 * 1024)
            writer.writerow(
                [
                    name,
                    document.original_filename,
                    document.document_date.isoformat() if document.document_date else "",
                    document.title or "",
                    " | ".join(tag_paths.get(document.id, [])),
                    document.sha256,
                ]
            )
        # BOM so Excel opens the Polish characters correctly
        archive.writestr("index.csv", "﻿" + index.getvalue())


def make_open_copy(storage: Storage, document: Document, workdir: Path) -> Path:
    """Read-only copy with a readable name in ``workdir``; old copies are removed first.

    Opening a copy (not the archived file) guarantees that an editor can never change the
    archive, even if the user presses "save".
    """
    _cleanup(workdir, older_than=24 * 3600)
    folder = workdir / uuid.uuid4().hex
    os.makedirs(lp(folder))
    target = folder / readable_name(document)
    with storage.open(document.stored_path) as src, open(lp(target), "wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    os.chmod(lp(target), stat.S_IREAD)
    return target


def _cleanup(workdir: Path, older_than: float) -> None:
    if not os.path.isdir(lp(workdir)):
        return
    now = time.time()
    for entry in os.scandir(lp(workdir)):
        if entry.is_dir() and now - entry.stat().st_mtime > older_than:
            for root, _dirs, files in os.walk(entry.path):
                for name in files:
                    os.chmod(os.path.join(root, name), stat.S_IWRITE)  # copies are read-only
            shutil.rmtree(entry.path, ignore_errors=True)
