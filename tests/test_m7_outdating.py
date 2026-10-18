"""M7: outdating documents (never deleting them) and restoring them."""

import io
import os
import uuid
from datetime import date

import pytest
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.ingest import Ingestor
from tabularium.models import AuditLog, Document, DocumentRelation, DocumentTag, RelationType, Tag
from tabularium.outdated import OutdatedDocument, OutdatedStore
from tabularium.services import DomainError
from tabularium.services import outdating as outdating_module
from tabularium.services.documents import add_tag, create_document, set_retain_until, update_details
from tabularium.services.entities import (
    add_membership,
    create_entity,
    delete_entity,
    update_entity,
)
from tabularium.services.outdating import outdate, restore_to_active
from tabularium.services.tags import create_tag, delete_tag, rename_tag, tag_paths
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"
TODAY = date(2031, 6, 1)


@pytest.fixture
def store(tmp_path, config):
    s = OutdatedStore(tmp_path / "outdated.db", config.paths.outdated)
    yield s
    s.dispose()


@pytest.fixture
def archive(config):
    return LocalStorage(config.paths.archive)


def put(session, archive, name, data, *, keep_until=date(2030, 12, 31), title=None):
    staged = archive.stage(io.BytesIO(data))
    key = storage_key(staged.sha256, os.path.splitext(name)[1])
    archive.commit(staged, key)
    doc = create_document(
        session,
        sha256=staged.sha256,
        stored_path=key,
        original_filename=name,
        size_bytes=len(data),
        mime_type="application/pdf",
        retention_years=5,
    )
    set_retain_until(session, doc, keep_until, retention_years=5)
    if title:
        update_details(session, doc, title=title)
    return doc


@pytest.fixture
def world(session, archive):
    anna = create_entity(session, etype(session, "Person"), "Anna Nowak")
    update_entity(session, anna, attributes={"tax id": "000"})
    family = create_entity(session, etype(session, "Other"), "Nowak Family")
    add_membership(session, anna, family)
    sender, category = create_tag(session, "sender"), create_tag(session, "category")
    s_anna = create_tag(session, parent=sender, entity=anna)
    bills = create_tag(session, "bills", parent=category)
    old = put(session, archive, "old.pdf", b"%PDF old", title="Rachunek 2024")
    payment = put(session, archive, "pay.pdf", b"%PDF pay", keep_until=date(2099, 1, 1))
    add_tag(session, old, s_anna)
    add_tag(session, old, bills)
    session.add(
        DocumentRelation(
            from_document_id=payment.id, to_document_id=old.id, type=RelationType.PAYMENT_FOR
        )
    )
    session.commit()
    return {
        "old": old,
        "payment": payment,
        "anna": anna,
        "s_anna": s_anna,
        "bills": bills,
        "sender": sender,
        "category": category,
    }


def test_outdating_is_blocked_until_after_keep_until(session, archive, store, world):
    with pytest.raises(DomainError, match="kept until 2099-01-01"):
        outdate(session, archive, store, world["payment"], today=TODAY)
    with pytest.raises(DomainError, match="kept until 2030-12-31"):
        outdate(session, archive, store, world["old"], today=date(2030, 12, 31))


def test_outdate_moves_file_and_snapshots_everything(session, archive, store, world, config):
    old = world["old"]
    key, doc_id = old.stored_path, old.id
    outdate(session, archive, store, old, today=TODAY, reason="retention over")

    assert not archive.exists(key)
    assert store.storage.exists(key) and store.storage.sha256(key) == old.sha256
    assert session.get(Document, doc_id) is None
    assert session.scalars(select(DocumentTag).where(DocumentTag.document_id == doc_id)).all() == []
    assert session.scalars(select(DocumentRelation)).all() == []
    # history in the active archive is kept (audit rows are never deleted)
    actions = session.scalars(
        select(AuditLog.action).where(AuditLog.object_id == str(doc_id)).order_by(AuditLog.id)
    ).all()
    assert actions[-1] == "OUTDATE" and actions[0] == "CREATE"

    with store.session_factory() as s:
        row = s.get(OutdatedDocument, doc_id)
        assert row.reason == "retention over" and row.display_name == "Rachunek 2024"
        assert row.tag_paths == ["category / bills", "sender / Anna Nowak"]
        anna = row.snapshot["tags"][1]["entity"]
        assert anna["attributes"] == {"tax id": "000"} and anna["member_of"] == ["Nowak Family"]
        assert row.snapshot["relations"][0]["type"] == "PAYMENT_FOR"
        assert row.snapshot["relations"][0]["direction"] == "from"
        assert [h["action"] for h in row.snapshot["history"]][:2] == ["CREATE", "UPDATE"]
    assert (config.paths.outdated / "metadata.json").exists()


def test_snapshot_survives_later_changes_in_the_archive(session, archive, store, world):
    outdate(session, archive, store, world["old"], today=TODAY)
    rename_tag(session, world["bills"], "invoices")
    update_entity(session, world["anna"], name="Anna Kowalska")
    session.commit()
    with store.session_factory() as s:
        row = s.get(OutdatedDocument, world["old"].id)
        assert row.tag_paths == ["category / bills", "sender / Anna Nowak"]


def test_restore_brings_document_tags_and_relations_back(session, archive, store, world):
    old = world["old"]
    doc_id, key = old.id, old.stored_path
    outdate(session, archive, store, old, today=TODAY)
    restored = restore_to_active(session, archive, store, doc_id)

    assert restored.id == doc_id and restored.title == "Rachunek 2024"
    assert restored.retain_until == date(2030, 12, 31) and restored.retain_until_manual
    assert archive.exists(key) and not store.storage.exists(key)
    paths = tag_paths(session)
    linked = session.scalars(select(DocumentTag.tag_id).where(DocumentTag.document_id == doc_id))
    assert sorted(paths[t] for t in linked) == ["category / bills", "sender / Anna Nowak"]
    rel = session.scalars(select(DocumentRelation)).one()
    assert (rel.from_document_id, rel.to_document_id) == (world["payment"].id, doc_id)
    with store.session_factory() as s:
        assert s.get(OutdatedDocument, doc_id) is None
    actions = session.scalars(
        select(AuditLog.action).where(AuditLog.object_id == str(doc_id)).order_by(AuditLog.id)
    ).all()
    assert "OUTDATE" in actions and actions[-3:] == ["RESTORE", "TAG_ADD", "TAG_ADD"]


def test_restore_recreates_tags_and_entities_deleted_meanwhile(session, archive, store, world):
    old = world["old"]
    outdate(session, archive, store, old, today=TODAY)
    delete_tag(session, world["s_anna"])
    delete_tag(session, world["bills"])
    delete_entity(session, world["anna"])
    session.commit()

    restore_to_active(session, archive, store, old.id)
    paths = tag_paths(session)
    linked = session.scalars(select(DocumentTag.tag_id).where(DocumentTag.document_id == old.id))
    assert sorted(paths[t] for t in linked) == ["category / bills", "sender / Anna Nowak"]
    anna_tag = session.scalars(select(Tag).where(Tag.name == "Anna Nowak")).one()
    assert anna_tag.entity.attributes == {"tax id": "000"}  # entity recreated from snapshot


def test_crash_before_any_database_change_leaves_everything_intact(
    session, archive, store, world, monkeypatch
):
    old = world["old"]
    real = store.session_factory

    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(store, "session_factory", broken)
    with pytest.raises(OSError):
        outdate(session, archive, store, old, today=TODAY)
    session.rollback()
    assert session.get(Document, old.id) is not None and archive.exists(old.stored_path)

    monkeypatch.setattr(store, "session_factory", real)  # retry works
    outdate(session, archive, store, old, today=TODAY)
    with store.session_factory() as s:
        assert s.get(OutdatedDocument, old.id) is not None


def test_crash_after_outdated_row_before_active_commit_is_retried(
    session, archive, store, world, monkeypatch
):
    old = world["old"]

    def broken(*args, **kwargs):
        raise RuntimeError("power cut")

    monkeypatch.setattr(outdating_module, "record", broken)
    with pytest.raises(RuntimeError):
        outdate(session, archive, store, old, today=TODAY)
    session.rollback()
    assert session.get(Document, old.id) is not None  # still active
    monkeypatch.undo()
    outdate(session, archive, store, session.get(Document, old.id), today=TODAY)
    with store.session_factory() as s:
        assert len(s.scalars(select(OutdatedDocument)).all()) == 1


def test_ingesting_an_outdated_file_again_is_a_duplicate(
    session, archive, store, world, config, session_factory
):
    outdate(session, archive, store, world["old"], today=TODAY)
    config.ingest.stable_seconds = 0
    (config.paths.ingest / "again.pdf").write_bytes(b"%PDF old")
    ingestor = Ingestor(config, session_factory, archive, is_outdated=store.has_hash)
    assert ingestor.scan().duplicates == ["again.pdf"]


def test_outdated_metadata_rebuilds_the_store(session, archive, store, world, tmp_path, config):
    outdate(session, archive, store, world["old"], today=TODAY)
    fresh = OutdatedStore(tmp_path / "new-pc-outdated.db", config.paths.outdated)
    try:
        assert fresh.restore_metadata() == 1
        with fresh.session_factory() as s:
            row = s.get(OutdatedDocument, world["old"].id)
            assert row.tag_paths == ["category / bills", "sender / Anna Nowak"]
        assert fresh.restore_metadata() == 0  # not into a filled store
    finally:
        fresh.dispose()


# --- web --------------------------------------------------------------------------------


@pytest.fixture
def client(config, session_factory, store):
    app = create_app(
        config,
        csrf_token=TOKEN,
        session_factory=session_factory,
        scan_on_startup=False,
        outdated_store=store,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_outdate_and_restore_through_the_ui(client, world, monkeypatch):
    old_id, pay_id = str(world["old"].id), str(world["payment"].id)
    assert "Must be kept until 2099-01-01" in client.get(f"/documents/{pay_id}").text

    import tabularium.web.routes_documents as doc_routes
    import tabularium.web.routes_outdated as routes

    class FakeDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(routes, "date", FakeDate)
    monkeypatch.setattr(doc_routes, "date", FakeDate)
    assert "Outdate this document" in client.get(f"/documents/{old_id}").text

    r = client.post(f"/documents/{old_id}/outdate", data={"reason": "done"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == f"/outdated?id={old_id}"
    assert client.get(f"/documents/{old_id}").status_code == 404
    assert "Rachunek" not in client.get("/search").text

    page = client.get(f"/outdated?id={old_id}").text
    assert "Rachunek 2024" in page and "sender / Anna Nowak" in page and "member of" in page
    assert client.get(f"/outdated/{old_id}/file").content == b"%PDF old"
    assert "1 of 1 outdated" in client.get("/outdated", params={"q": "anna"}).text
    assert "0 of 1 outdated" in client.get("/outdated", params={"q": "zzz"}).text

    r = client.post(f"/outdated/{old_id}/restore")
    assert r.headers["HX-Redirect"] == f"/documents/{old_id}"
    assert "Rachunek 2024" in client.get(f"/documents/{old_id}").text


def test_outdate_too_early_shows_error(client, world):
    r = client.post(f"/documents/{world['payment'].id}/outdate")
    assert r.status_code == 200 and "kept until 2099-01-01" in r.text


def test_unknown_outdated_document(client):
    assert client.get(f"/outdated/{uuid.uuid4()}/file").status_code == 404


def test_empty_outdated_store_never_overwrites_its_metadata(
    session, archive, store, world, tmp_path, config
):
    """outdated.db lost or not restored yet: its metadata.json is the only copy."""
    outdate(session, archive, store, world["old"], today=TODAY)
    meta = config.paths.outdated / "metadata.json"
    before = meta.read_bytes()

    lost = OutdatedStore(tmp_path / "lost-outdated.db", config.paths.outdated)
    try:
        assert lost.restore_pending()
        payment = world["payment"]
        set_retain_until(session, payment, date(2020, 1, 1), retention_years=5)
        session.commit()
        with pytest.raises(DomainError, match="Restore the outdated store"):
            outdate(session, archive, lost, payment, today=TODAY)
        with pytest.raises(DomainError, match="Restore the outdated store"):
            lost.write_metadata()
        assert meta.read_bytes() == before
        assert session.get(Document, payment.id) is not None  # nothing moved

        assert lost.restore_metadata() == 1
        assert not lost.restore_pending()
        outdate(session, archive, lost, session.get(Document, payment.id), today=TODAY)
        with lost.session_factory() as s:
            assert len(s.scalars(select(OutdatedDocument)).all()) == 2
    finally:
        lost.dispose()


def test_history_links_outdated_documents(client, world, session, archive, store):
    outdate(session, archive, store, world["old"], today=TODAY)
    page = client.get("/history").text
    assert f'<a href="/outdated?id={world["old"].id}">Rachunek 2024 (outdated)</a>' in page


def test_maintenance_offers_outdated_store_restore(
    config, session_factory, session, archive, world, tmp_path
):
    first = OutdatedStore(tmp_path / "first.db", config.paths.outdated)
    outdate(session, archive, first, world["old"], today=TODAY)
    first.dispose()
    lost = OutdatedStore(tmp_path / "lost.db", config.paths.outdated)
    app = create_app(
        config,
        csrf_token=TOKEN,
        session_factory=session_factory,
        scan_on_startup=False,
        outdated_store=lost,
    )
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})
    assert "Restore the outdated store" in c.get("/maintenance").text
    r = c.post("/maintenance/restore-outdated")
    assert "Outdated store restored." in r.text
    assert "Restore the outdated store" not in c.get("/maintenance").text
    lost.dispose()
