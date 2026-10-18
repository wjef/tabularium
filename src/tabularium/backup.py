"""Database backups in ``<app home>/backups`` (see docs/requirements.md §10)."""

import logging
import os
from pathlib import Path

from tabularium import db

log = logging.getLogger(__name__)
PREFIX = "tabularium-"


def list_backups(backup_dir: Path) -> list[Path]:
    """Backups, newest first (names start with a sortable timestamp)."""
    if not backup_dir.is_dir():
        return []
    files = [p for p in backup_dir.iterdir() if p.name.startswith(PREFIX) and p.suffix == ".db"]
    return sorted(files, key=lambda p: p.name, reverse=True)


def prune(backup_dir: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest backups; returns the deleted files."""
    removed = list_backups(backup_dir)[keep:]
    for path in removed:
        os.unlink(path)
    return removed


def backup_now(database: Path, backup_dir: Path, keep: int, label: str = "startup") -> Path | None:
    """Back up the database (if it exists) and apply retention."""
    if not database.exists():
        return None
    target = db.backup_sqlite(database, backup_dir, label)
    removed = prune(backup_dir, keep)
    log.info("Database backed up to %s (%d old backup(s) removed)", target, len(removed))
    return target
