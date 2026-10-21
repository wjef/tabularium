"""Supporting dates: when a document was sent and when it was received.

The existing ``document_date`` stays the main date (date created). New columns start empty.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sent_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("received_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_column("received_date")
        batch_op.drop_column("sent_date")
