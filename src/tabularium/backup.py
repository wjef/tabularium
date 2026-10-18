"""Database backups in ``<app home>/backups`` (see docs/requirements.md §10).

Retention applies to startup backups only. Backups made before a database upgrade and manual
backups are kept until the user removes them: they are the ones that matter after a mistake.
"""

import logging
import os
from pathlib import Path

from tabularium import db

log = logging.getLogger(__name__)
PREFIX = "tabularium-"
STARTUP = "startup"


def list_backups(backup_dir: Path, label: str | None = None) -> list[Path]:
    """Backups (optionally only one label), newest first (names start with a timestamp)."""
    if not backup_dir.is_dir():
        return []
    suffix = f"-{label}.db" if label else ".db"
    files = [
        p for p in backup_dir.iterdir() if p.name.startswith(PREFIX) and p.name.endswith(suffix)
    ]
    return sorted(files, key=lambda p: p.name, reverse=True)


def prune(backup_dir: Path, keep: int, label: str = STARTUP) -> list[Path]:
    """Delete all but the ``keep`` newest backups with ``label``; returns the deleted files."""
    removed = list_backups(backup_dir, label)[keep:]
    for path in removed:
        os.unlink(path)
    return removed


def _has_user_data(database: Path) -> bool:
    from tabularium.metadata import is_empty  # local import: metadata imports models

    engine = db.make_engine(db.sqlite_url(database))
    try:
        with db.make_session_factory(engine)() as session:
            return not is_empty(session)
    finally:
        engine.dispose()


def backup_now(database: Path, backup_dir: Path, keep: int, label: str = STARTUP) -> Path | None:
    """Back up the database. Startup backups skip an empty database (so a lost database cannot
    push the last good backups out) and are pruned to the newest ``keep``."""
    if not database.exists():
        return None
    if label == STARTUP and not _has_user_data(database):
        log.info("Database is empty; startup backup skipped")
        return None
    target = db.backup_sqlite(database, backup_dir, label)
    removed = prune(backup_dir, keep) if label == STARTUP else []
    log.info("Database backed up to %s (%d old backup(s) removed)", target, len(removed))
    return target
