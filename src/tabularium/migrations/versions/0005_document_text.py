"""Full-text search: extracted text per document plus an FTS5 index.

Existing documents get no row; the background indexer picks up every document without one.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tabularium.db

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_text",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "DONE",
                "EMPTY",
                "NEEDS_OCR",
                "FAILED",
                name="text_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=20), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("ocr_pages", sa.Integer(), nullable=True),
        sa.Column("chars", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extracted_at", tabularium.db.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_document_text_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_document_text")),
    )
    with op.batch_alter_table("document_text", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_document_text_status"), ["status"], unique=False)

    if op.get_bind().dialect.name == "sqlite":
        # Normalized text (lower case, no diacritics) is stored, so the default tokenizer is
        # enough. document_id is the document's UUID as 32 hex characters.
        op.execute("CREATE VIRTUAL TABLE document_fts USING fts5(document_id UNINDEXED, body)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS document_fts")
    with op.batch_alter_table("document_text", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_text_status"))
    op.drop_table("document_text")
