"""Tagging is finished explicitly: documents stay "To be tagged" until marked done.

Documents that already have at least one tag count as done (they had left the old
"To be tagged" view); documents without tags stay to be tagged.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

import tabularium.db

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tagging_done_at", tabularium.db.UTCDateTime(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_document_tagging_done_at"), ["tagging_done_at"], unique=False
        )

    document = sa.table(
        "document",
        sa.column("id", sa.Uuid()),
        sa.column("tagging_done_at", tabularium.db.UTCDateTime()),
    )
    document_tag = sa.table("document_tag", sa.column("document_id", sa.Uuid()))
    has_tag = sa.exists().where(document_tag.c.document_id == document.c.id)
    op.execute(document.update().where(has_tag).values(tagging_done_at=datetime.now(UTC)))


def downgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_tagging_done_at"))
        batch_op.drop_column("tagging_done_at")
