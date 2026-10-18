"""Archive storage (see docs/adr/0003 and docs/adr/0009).

All access to archived files goes through a ``Storage``. ``LocalStorage`` keeps files in a
folder as ``<2 hex>/<sha256>.<ext>``; a cloud implementation can follow the same protocol.
Files are written once and never modified.
"""

import contextlib
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from tabularium.fsutil import lp

CHUNK = 1024 * 1024
_EXT_RE = re.compile(r"^\.[a-z0-9]{1,15}$")


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StagedFile:
    """A copy written to the staging area, not yet visible in the archive."""

    sha256: str
    size: int
    temp_path: Path


def storage_key(sha256: str, extension: str) -> str:
    """Archive-relative key, e.g. ``3f/3fa9…e81b.pdf``. Unusual extensions are dropped."""
    ext = extension.lower()
    ext = ext if _EXT_RE.match(ext) else ""
    return f"{sha256[:2]}/{sha256}{ext}"


def sha256_of(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


class Storage(Protocol):
    def stage(self, source: BinaryIO) -> StagedFile: ...
    def commit(self, staged: StagedFile, key: str) -> bool: ...
    def discard(self, staged: StagedFile) -> None: ...
    def cleanup_staging(self, older_than: float, now: float) -> int: ...
    def remove_uncommitted(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def open(self, key: str) -> BinaryIO: ...
    def sha256(self, key: str) -> str: ...


class LocalStorage:
    STAGING = ".tmp"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if ".." in key.split("/") or key.startswith("/"):
            raise StorageError(f"invalid storage key: {key}")
        return self.root.joinpath(*key.split("/"))

    def stage(self, source: BinaryIO) -> StagedFile:
        """Copy ``source`` into the staging area, hashing in the same pass."""
        staging = self.root / self.STAGING
        os.makedirs(lp(staging), exist_ok=True)
        temp_path = staging / f"{uuid.uuid4().hex}.part"
        digest, size = hashlib.sha256(), 0
        try:
            with open(lp(temp_path), "xb") as out:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            _unlink_quietly(temp_path)
            raise
        return StagedFile(digest.hexdigest(), size, temp_path)

    def commit(self, staged: StagedFile, key: str) -> bool:
        """Move a staged file to ``key`` and verify it.

        Returns True if the file was newly created, False if an identical file was already
        there (e.g. left behind by an interrupted earlier run); the staged copy is then dropped.
        """
        target = self._path(key)
        os.makedirs(lp(target.parent), exist_ok=True)
        if os.path.exists(lp(target)):
            if self.sha256(key) != staged.sha256:
                raise StorageError(f"{key} exists with different content")
            self.discard(staged)
            return False
        os.rename(lp(staged.temp_path), lp(target))  # same volume: atomic, never overwrites
        if self.sha256(key) != staged.sha256:
            raise StorageError(f"{key} failed verification after writing")
        return True

    def discard(self, staged: StagedFile) -> None:
        _unlink_quietly(staged.temp_path)

    def cleanup_staging(self, older_than: float, now: float) -> int:
        """Remove staged copies left by interrupted runs. Returns how many were removed."""
        staging = self.root / self.STAGING
        removed = 0
        if not os.path.isdir(lp(staging)):
            return 0
        for entry in os.scandir(lp(staging)):
            if entry.name.endswith(".part") and now - entry.stat().st_mtime > older_than:
                _unlink_quietly(Path(entry.path))
                removed += 1
        return removed

    def remove_uncommitted(self, key: str) -> None:
        """Undo a ``commit`` whose database record could not be written."""
        _unlink_quietly(self._path(key))

    def local_path(self, key: str) -> Path:
        """Where the file lives on disk (local storage only; e.g. for "Show in Explorer")."""
        return self._path(key)

    def exists(self, key: str) -> bool:
        return os.path.isfile(lp(self._path(key)))

    def open(self, key: str) -> BinaryIO:
        return open(lp(self._path(key)), "rb")  # noqa: SIM115 — caller closes

    def sha256(self, key: str) -> str:
        with self.open(key) as stream:
            return sha256_of(stream)


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(lp(path))
