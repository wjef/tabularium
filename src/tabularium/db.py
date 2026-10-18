"""Database engine, sessions and migrations (SQLAlchemy 2 + Alembic, see docs/adr/0009)."""

from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import DateTime, Engine, MetaData, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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
