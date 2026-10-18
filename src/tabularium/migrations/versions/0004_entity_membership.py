"""Entities can belong to other entities (person → family, family → extended family).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tabularium.db

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_membership",
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", tabularium.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint("member_id <> group_id", name=op.f("ck_entity_membership_not_self")),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["entity.id"],
            name=op.f("fk_entity_membership_group_id_entity"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["entity.id"],
            name=op.f("fk_entity_membership_member_id_entity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("member_id", "group_id", name=op.f("pk_entity_membership")),
    )
    with op.batch_alter_table("entity_membership", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_entity_membership_group_id"), ["group_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("entity_membership", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_entity_membership_group_id"))
    op.drop_table("entity_membership")
