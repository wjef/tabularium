from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

import tabularium.models  # noqa: F401
from tabularium import db

EXPECTED_TABLES = {
    "audit_log",
    "document",
    "document_relation",
    "document_tag",
    "entity",
    "ingest_log",
    "tag",
}


def _head(url: str) -> str:
    return ScriptDirectory.from_config(db.alembic_config(url)).get_current_head()


def test_migrations_create_all_tables_and_are_idempotent(db_url):
    db.upgrade(db_url)  # second run is a no-op
    engine = db.make_engine(db_url)
    with engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) >= EXPECTED_TABLES
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == _head(db_url)
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_models_match_migrations(db_url):
    """Fails when a model changed without a migration."""
    engine = db.make_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_to_base_and_upgrade_again(db_url):
    cfg = db.alembic_config(db_url)
    command.downgrade(cfg, "base")
    engine = db.make_engine(db_url)
    with engine.connect() as conn:
        assert not EXPECTED_TABLES & set(inspect(conn).get_table_names())
    engine.dispose()
    db.upgrade(db_url)
