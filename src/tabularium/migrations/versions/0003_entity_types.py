"""Entity types become user-defined data instead of a fixed list.

The four former fixed types are created as editable rows and existing entities are
linked to them.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

import tabularium.db

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# former fixed value → editable starting type (code, display name, sort order)
DEFAULT_TYPES = [
    ("PERSON", "Person", 10),
    ("ORGANIZATION", "Organization", 20),
    ("INSTITUTION", "Institution", 30),
    ("OTHER", "Other", 40),
]

entity_type_table = sa.table(
    "entity_type",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("normalized_name", sa.String()),
    sa.column("sort_order", sa.Integer()),
    sa.column("created_at", tabularium.db.UTCDateTime()),
)
entity_table = sa.table(
    "entity",
    sa.column("type", sa.String()),
    sa.column("type_id", sa.Uuid()),
)


def _remove_leftovers_of_failed_attempt() -> None:
    """Before migrations ran in a transaction, a failed attempt could leave an empty
    ``entity_type`` table (and a batch-mode temp table) behind while still at 0002."""
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    if "_alembic_tmp_entity" in tables:
        op.drop_table("_alembic_tmp_entity")
    if "entity_type" in tables:
        rows = conn.execute(sa.text("SELECT count(*) FROM entity_type")).scalar()
        if rows:
            raise RuntimeError(
                "Found a non-empty entity_type table at revision 0002; not touching it. "
                "Restore the backup from before the upgrade."
            )
        op.drop_table("entity_type")


def upgrade() -> None:
    _remove_leftovers_of_failed_attempt()
    op.create_table(
        "entity_type",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", tabularium.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_type")),
        sa.UniqueConstraint("normalized_name", name=op.f("uq_entity_type_normalized_name")),
    )
    now = datetime.now(UTC)
    ids = {code: uuid.uuid4() for code, _, _ in DEFAULT_TYPES}
    op.bulk_insert(
        entity_type_table,
        [
            {
                "id": ids[code],
                "name": name,
                "normalized_name": name.lower(),
                "sort_order": order,
                "created_at": now,
            }
            for code, name, order in DEFAULT_TYPES
        ],
    )

    with op.batch_alter_table("entity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("type_id", sa.Uuid(), nullable=True))
    for code, type_id in ids.items():
        op.execute(entity_table.update().where(entity_table.c.type == code).values(type_id=type_id))
    # anything unexpected becomes "Other" rather than blocking the upgrade
    op.execute(
        entity_table.update().where(entity_table.c.type_id.is_(None)).values(type_id=ids["OTHER"])
    )
    with op.batch_alter_table("entity", schema=None) as batch_op:
        batch_op.alter_column("type_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_index(batch_op.f("ix_entity_type_id"), ["type_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_entity_type_id_entity_type"),
            "entity_type",
            ["type_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_column("type")


def downgrade() -> None:
    with op.batch_alter_table("entity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("type", sa.String(length=32), nullable=True))
    conn = op.get_bind()
    names = dict(conn.execute(sa.select(entity_type_table.c.id, entity_type_table.c.name)).all())
    by_name = {name.lower(): code for code, name, _ in DEFAULT_TYPES}
    for type_id, name in names.items():
        code = by_name.get(name.lower(), "OTHER")
        op.execute(entity_table.update().where(entity_table.c.type_id == type_id).values(type=code))
    with op.batch_alter_table("entity", schema=None) as batch_op:
        batch_op.alter_column("type", existing_type=sa.String(length=32), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_entity_type_id_entity_type"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_entity_type_id"))
        batch_op.drop_column("type_id")
    op.drop_table("entity_type")
