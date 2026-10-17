from sqlalchemy import inspect, text

from tabularium import db


def test_migrations_run_on_fresh_database(config):
    config.database_path.parent.mkdir(parents=True)
    url = db.sqlite_url(config.database_path)
    db.upgrade(url)
    db.upgrade(url)  # idempotent

    engine = db.make_engine(url)
    with engine.connect() as conn:
        assert "alembic_version" in inspect(conn).get_table_names()
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()
