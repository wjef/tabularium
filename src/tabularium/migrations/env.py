from alembic import context
from sqlalchemy import event, pool

import tabularium.models  # noqa: F401 — registers the tables on Base.metadata
from tabularium.db import Base, make_engine

config = context.config
target_metadata = Base.metadata


class ForeignKeyCheckError(RuntimeError):
    pass


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _sqlite_for_migrations(engine) -> None:
    """Make SQLite migrations safe.

    - Foreign keys off: batch mode rebuilds a table (copy, drop, rename); with foreign keys on,
      dropping a table that other rows point to fails. They are checked after every migration.
    - Real transactions (explicit BEGIN, pysqlite's own handling disabled), so DDL is
      transactional and a failed migration leaves nothing half-done.
    """

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys = OFF")

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")


def _check_foreign_keys(connection) -> None:
    problems = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise ForeignKeyCheckError(f"Migration left broken references: {problems[:10]}")


def run_migrations_online() -> None:
    engine = make_engine(config.get_main_option("sqlalchemy.url"), poolclass=pool.NullPool)
    is_sqlite = engine.dialect.name == "sqlite"
    if is_sqlite:
        _sqlite_for_migrations(engine)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
            transactional_ddl=True,
            transaction_per_migration=True,
            on_version_apply=(
                (lambda ctx, step, heads, run_args: _check_foreign_keys(connection))
                if is_sqlite
                else None
            ),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
