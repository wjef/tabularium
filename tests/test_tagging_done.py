"""Tagging is finished explicitly: documents stay "To be tagged" until marked done."""

import uuid

from alembic import command
from sqlalchemy import text

from tabularium import db


def _insert_document(conn, sha: str) -> str:
    doc_id = uuid.uuid4().hex
    conn.execute(
        text(
            "INSERT INTO document (id, sha256, stored_path, original_filename, mime_type,"
            " size_bytes, retain_until, retain_until_manual, ingested_at) VALUES"
            " (:id, :sha, :path, 'scan.pdf', 'application/pdf', 10, '2031-12-31', 0,"
            " '2026-10-01 10:00:00')"
        ),
        {"id": doc_id, "sha": sha, "path": f"{sha[:2]}/{sha}.pdf"},
    )
    return doc_id


def test_migration_0006_marks_tagged_documents_done(tmp_path):
    url = db.sqlite_url(tmp_path / "x.db")
    cfg = db.alembic_config(url)
    command.upgrade(cfg, "0005")
    engine = db.make_engine(url)
    with engine.begin() as conn:
        tagged = _insert_document(conn, "a" * 64)
        untagged = _insert_document(conn, "b" * 64)
        tag_id = uuid.uuid4().hex
        conn.execute(
            text(
                "INSERT INTO tag (id, parent_id, name, normalized_name, sort_order, created_at)"
                " VALUES (:id, NULL, 'FV', 'fv', 0, '2026-10-01 10:00:00')"
            ),
            {"id": tag_id},
        )
        conn.execute(
            text(
                "INSERT INTO document_tag (document_id, tag_id, tagged_at)"
                " VALUES (:d, :t, '2026-10-02 10:00:00')"
            ),
            {"d": tagged, "t": tag_id},
        )
    engine.dispose()

    db.upgrade(url)
    engine = db.make_engine(url)
    with engine.connect() as conn:
        done = dict(conn.execute(text("SELECT id, tagging_done_at FROM document")).all())
    engine.dispose()
    assert done[tagged] is not None and done[untagged] is None

    command.downgrade(cfg, "0005")  # and back again
    db.upgrade(url)
