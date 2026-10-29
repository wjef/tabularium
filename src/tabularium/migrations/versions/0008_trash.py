"""Trash: documents the user put aside (added by mistake, irrelevant). Hidden, never deleted.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tabularium.db

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trashed_at", tabularium.db.UTCDateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_document_trashed_at"), ["trashed_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_trashed_at"))
        batch_op.drop_column("trashed_at")
