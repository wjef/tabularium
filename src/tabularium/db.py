"""Database engine, sessions and migrations (SQLAlchemy 2 + Alembic, see docs/adr/0009)."""

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Engine, MetaData, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from tabularium.text import normalize

# Stable constraint names, so migrations work identically on SQLite and PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo otherwise).

    Naive datetimes are rejected so local times can never slip in unnoticed.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_engine(url: str, **kwargs) -> Engine:
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")  # safe: the database is on a local disk
    cursor.close()
    # tnorm(x): the app's own normalization (case, diacritics, ł → l) for text search.
    # A PostgreSQL backend would need an equivalent function (e.g. unaccent + lower).
    dbapi_connection.create_function(
        "tnorm", 1, lambda v: None if v is None else normalize(v), deterministic=True
    )


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(resources.files("tabularium") / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade(url: str) -> None:
    """Bring the database schema to the latest migration."""
    command.upgrade(alembic_config(url), "head")


def current_revision(url: str) -> str | None:
    engine = make_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision(url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def backup_sqlite(path: Path, backup_dir: Path, label: str) -> Path:
    """Consistent copy of a SQLite database (online backup API, includes the WAL)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"tabularium-{stamp}-{label}.db"
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return target


def upgrade_with_backup(path: Path, backup_dir: Path) -> Path | None:
    """Back up an existing database before applying pending migrations, then upgrade.

    Returns the backup path, or None when nothing needed migrating.
    """
    url = sqlite_url(path)
    backup = None
    if path.exists():
        current = current_revision(url)
        if current != head_revision(url):
            backup = backup_sqlite(path, backup_dir, f"before-upgrade-from-{current or 'empty'}")
    upgrade(url)
    return backup
