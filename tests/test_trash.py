"""Trash: documents put aside by the user. Hidden everywhere except Trash, never deleted."""

from datetime import date

import pytest
from sqlalchemy import select
from test_documents import make_doc

from tabularium.ingest import Ingestor
from tabularium.models import IngestLog
from tabularium.services import DomainError
from tabularium.services.documents import (
    add_tag,
    inbox_documents,
    set_retain_until,
    set_tagging_done,
    set_trashed,
)
from tabularium.services.search import SearchQuery, search
from tabularium.services.tags import create_tag, delete_tag, subtree_document_counts
from tabularium.storage import LocalStorage


def found(session, **query):
    return search(session, SearchQuery(**query))[0]


def test_trash_hides_a_document_and_restore_brings_it_back_as_it_was(session):
    kept, binned = make_doc(session, b"kept"), make_doc(session, b"binned")
    tag = create_tag(session, "category")
    for doc in (kept, binned):
        add_tag(session, doc, tag)
        set_tagging_done(session, doc, True)
    todo = make_doc(session, b"todo")
    session.commit()

    assert set_trashed(session, binned, True) is True
    assert set_trashed(session, binned, True) is False  # already there
    assert set_trashed(session, todo, True) is True
    session.commit()
    assert found(session) == [kept]
    assert found(session, tagging="done") == [kept]
    assert found(session, tag_ids=[tag.id]) == [kept]
    assert set(found(session, trash=True)) == {binned, todo}
    assert session.scalars(inbox_documents()).all() == []  # the trashed one is not "to be tagged"
    assert subtree_document_counts(session, done_only=True)[tag.id] == 1

    assert set_trashed(session, binned, False) is True
    assert set_trashed(session, todo, False) is True
    session.commit()
    assert set(found(session, tagging="done")) == {kept, binned}  # tags and state unchanged
    assert session.scalars(inbox_documents()).all() == [todo]  # back in To be tagged
    assert found(session, trash=True) == []


def test_retention_does_not_block_trash(session):
    doc = make_doc(session)
    set_retain_until(session, doc, date(2099, 1, 1), retention_years=5)
    assert set_trashed(session, doc, True) is True


def test_a_tag_used_only_in_trash_still_cannot_be_deleted(session):
    doc = make_doc(session)
    tag = create_tag(session, "category")
    add_tag(session, doc, tag)
    set_trashed(session, doc, True)
    session.commit()
    with pytest.raises(DomainError, match="used by 1 document"):
        delete_tag(session, tag)


def test_dropping_a_trashed_file_again_is_a_duplicate_noted_as_in_trash(config, session_factory):
    config.ingest.stable_seconds = 0
    ingestor = Ingestor(config, session_factory, LocalStorage(config.paths.archive))
    (config.paths.ingest / "scan.pdf").write_bytes(b"%PDF mistake")
    assert ingestor.scan().imported == ["scan.pdf"]
    with session_factory() as s, s.begin():
        set_trashed(s, found(s)[0], True)

    (config.paths.ingest / "again.pdf").write_bytes(b"%PDF mistake")
    assert ingestor.scan().duplicates == ["again.pdf"]
    with session_factory() as s:
        rows = s.scalars(select(IngestLog.message).where(IngestLog.source_path == "again.pdf"))
        assert rows.all() == ["in Trash"]


def test_migration_0008_keeps_existing_documents_out_of_trash(tmp_path):
    from alembic import command
    from sqlalchemy import text
    from test_tagging_done import _insert_document

    from tabularium import db

    url = db.sqlite_url(tmp_path / "x.db")
    cfg = db.alembic_config(url)
    command.upgrade(cfg, "0007")
    engine = db.make_engine(url)
    with engine.begin() as conn:
        _insert_document(conn, "a" * 64)
        _insert_document(conn, "b" * 64)
    engine.dispose()

    db.upgrade(url)
    engine = db.make_engine(url)
    with engine.connect() as conn:
        trashed = conn.execute(text("SELECT trashed_at FROM document")).scalars().all()
    engine.dispose()
    assert trashed == [None, None]

    command.downgrade(cfg, "0007")  # and back again
    db.upgrade(url)
