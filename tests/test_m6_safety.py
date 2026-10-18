"""M6: backups, metadata.json export/restore, integrity check."""

import io
import json
import os
import time
from datetime import date

import pytest
from conftest import etype
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium import db, integrity, metadata
from tabularium.backup import backup_now, list_backups
from tabularium.models import AuditLog, DocumentRelation, RelationType
from tabularium.services import DomainError
from tabularium.services.documents import (
    add_tag,
    create_document,
    set_document_date,
    set_retain_until,
    update_details,
)
from tabularium.services.entities import add_membership, create_entity, update_entity
from tabularium.services.entity_types import create_type
from tabularium.services.tags import create_tag
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


def archive_doc(session, storage, name: str, data: bytes, **details):
    staged = storage.stage(io.BytesIO(data))
    key = storage_key(staged.sha256, os.path.splitext(name)[1])
    storage.commit(staged, key)
    doc = create_document(
        session,
        sha256=staged.sha256,
        stored_path=key,
        original_filename=name,
        size_bytes=len(data),
        mime_type="application/pdf",
        retention_years=5,
    )
    if details.get("title") or details.get("note"):
        update_details(session, doc, title=details.get("title"), note=details.get("note"))
    if details.get("when"):
        set_document_date(session, doc, details["when"], retention_years=5)
    return doc


@pytest.fixture
def rich(session, config):
    """A small but complete archive: every kind of row exists at least once."""
    storage = LocalStorage(config.paths.archive)
    bank = create_type(session, "Bank")
    jan = create_entity(session, etype(session, "Person"), "Jan Kowalski", aliases=["JK"])
    family = create_entity(session, etype(session, "Other"), "Kowalski Family")
    update_entity(session, jan, attributes={"e-mail": "jan@example.com"})
    add_membership(session, jan, family)
    example_bank = create_entity(session, bank, "Example Bank S.A.")
    sender, category = create_tag(session, "sender"), create_tag(session, "category")
    taxes = create_tag(session, "taxes", parent=category)
    pit = create_tag(session, "PIT", parent=taxes)
    s_bank = create_tag(session, parent=sender, entity=example_bank)
    d1 = archive_doc(
        session, storage, "a.pdf", b"%PDF a", title="Zeznanie PIT", when=date(2025, 4, 1)
    )
    d2 = archive_doc(session, storage, "b.pdf", b"%PDF b", note="Przelew – grudzień")
    set_retain_until(session, d2, date(2040, 1, 1), retention_years=5)
    add_tag(session, d1, pit)
    add_tag(session, d1, s_bank)
    add_tag(session, d2, s_bank)
    session.add(
        DocumentRelation(
            from_document_id=d2.id, to_document_id=d1.id, type=RelationType.PAYMENT_FOR
        )
    )
    session.commit()
    return {"storage": storage, "d1": d1, "d2": d2}


# --- backups ----------------------------------------------------------------------------


def test_startup_backups_are_pruned_but_upgrade_and_manual_backups_kept(
    tmp_path, db_url, session, rich
):
    database = tmp_path / "test.db"
    backups = tmp_path / "backups"
    db.backup_sqlite(database, backups, "before-upgrade-from-0003")
    assert backup_now(database, backups, keep=3, label="manual")
    for _ in range(5):
        assert backup_now(database, backups, keep=3)
        time.sleep(1.05)  # names carry a timestamp with seconds
    assert len(list_backups(backups, "startup")) == 3
    assert len(list_backups(backups, "manual")) == 1
    assert len(list_backups(backups, "before-upgrade-from-0003")) == 1
    assert backup_now(tmp_path / "missing.db", backups, keep=3) is None


def test_empty_database_is_not_backed_up_at_startup(tmp_path):
    database = tmp_path / "empty.db"
    db.upgrade(db.sqlite_url(database))  # seeded entity types only: no user data
    assert backup_now(database, tmp_path / "b", keep=3) is None
    assert backup_now(database, tmp_path / "b", keep=3, label="manual") is not None


# --- metadata.json ------------------------------------------------------------------------


def test_metadata_round_trip_rebuilds_an_identical_database(session, rich, tmp_path):
    revision = db.revision_of(session)
    original = metadata.dump(session, revision)
    path = metadata.write(tmp_path, original)
    assert json.loads(path.read_text(encoding="utf-8"))["counts"]["document"] == 2

    url = db.sqlite_url(tmp_path / "fresh.db")
    db.upgrade(url)
    engine = db.make_engine(url)
    with db.make_session_factory(engine)() as fresh:
        counts = metadata.restore(fresh, metadata.read(path), db.revision_of(fresh))
        fresh.commit()
        rebuilt = metadata.dump(fresh, db.revision_of(fresh))
    engine.dispose()

    assert counts["document"] == 2 and counts["tag"] == 5
    restore_entry = rebuilt["tables"]["audit_log"].pop()
    assert restore_entry["action"] == "RESTORE"
    assert rebuilt["tables"] == original["tables"]


def test_metadata_is_readable(session, rich):
    data = metadata.dump(session, db.revision_of(session))
    doc = next(d for d in data["tables"]["document"] if d["original_filename"] == "a.pdf")
    assert doc["_display_name"] == "Zeznanie PIT"
    assert doc["_tags"] == ["category / taxes / PIT", "sender / Example Bank S.A."]
    assert doc["document_date"] == "2025-04-01"
    assert doc["ingested_at"].endswith("+00:00")


def test_restore_refuses_unsafe_situations(session, rich, tmp_path):
    data = metadata.dump(session, db.revision_of(session))
    with pytest.raises(DomainError, match="empty database"):
        metadata.restore(session, data, db.revision_of(session))
    with pytest.raises(DomainError, match="database version"):
        metadata.restore(session, data, "9999")
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "something-else"}', encoding="utf-8")
    with pytest.raises(DomainError, match="not a Tabularium"):
        metadata.read(bad)


def test_exporter_writes_after_committed_changes(session_factory, config):
    exporter = metadata.MetadataExporter(
        session_factory,
        config.paths.archive,
        schema_revision=lambda: "0004",
        interval=3600,
    ).attach()
    assert not exporter.dirty.is_set()
    with session_factory() as s:
        create_tag(s, "category")
        s.rollback()
    assert not exporter.dirty.is_set()  # rolled back: nothing to export
    with session_factory() as s, s.begin():
        create_tag(s, "category")
    assert exporter.dirty.is_set()
    path = exporter.write_now()
    assert not exporter.dirty.is_set()
    assert metadata.read(path)["counts"]["tag"] == 1
    assert not (config.paths.archive / "metadata.json.tmp").exists()


# --- integrity check ------------------------------------------------------------------


def test_healthy_archive(session, rich, config):
    metadata.write(config.paths.archive, metadata.dump(session, "x"))  # app files are ignored
    report = integrity.check(session, config.paths.archive, full=True)
    assert report.healthy and report.ok == 2 and report.checked == 2


def test_problems_are_reported_never_fixed(session, rich, config):
    root = config.paths.archive
    d1, d2 = rich["d1"], rich["d2"]
    moved = root / "elsewhere.pdf"
    os.replace(root / d1.stored_path, moved)  # "missing" + an orphan with the same content
    path2 = root / d2.stored_path
    path2.write_bytes(b"%PDF X")  # same size, different content
    (root / "ab").mkdir(exist_ok=True)
    (root / "ab" / "stray-DESKTOP-ABC.pdf").write_bytes(b"conflict copy")

    quick = integrity.check(session, root)
    assert [p.path for p in quick.missing] == [d1.stored_path]
    assert quick.changed == []  # same size: quick check cannot see it
    full = integrity.check(session, root, full=True)
    assert [p.path for p in full.changed] == [d2.stored_path]
    orphans = {p.path: p for p in full.orphans}
    assert set(orphans) == {"elsewhere.pdf", "ab/stray-DESKTOP-ABC.pdf"}
    assert orphans["elsewhere.pdf"].document_id == str(d1.id)
    assert "same content" in orphans["elsewhere.pdf"].detail
    assert moved.exists() and path2.read_bytes() == b"%PDF X"  # nothing touched


def test_cloud_only_files_are_not_read(session, rich, config, monkeypatch):
    monkeypatch.setattr(integrity, "is_placeholder", lambda st: True)
    monkeypatch.setattr(integrity, "sha256_of", lambda f: pytest.fail("must not read"))
    report = integrity.check(session, config.paths.archive, full=True)
    assert len(report.cloud_only) == 2 and report.healthy


def test_size_change_is_found_by_quick_check(session, rich, config):
    (config.paths.archive / rich["d1"].stored_path).write_bytes(b"truncated")
    report = integrity.check(session, config.paths.archive)
    assert "size" in report.changed[0].detail


# --- maintenance page -------------------------------------------------------------------


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_maintenance_page_and_actions(client, rich, config):
    r = client.get("/maintenance")
    assert r.status_code == 200
    assert "Restore from metadata.json" not in r.text  # database is not empty

    r = client.post("/maintenance/check")
    assert "All good: 2 of 2 documents verified." in r.text
    r = client.post("/maintenance/metadata")
    assert "metadata.json written." in r.text
    assert (config.paths.archive / "metadata.json").exists()


def test_manual_backup(client, config):
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    config.database_path.write_bytes(b"")  # backup needs an existing file
    r = client.post("/maintenance/backup")
    assert "Backup created." in r.text
    assert "-manual.db" in r.text


def test_moving_to_a_new_pc(session, rich, config, tmp_path, monkeypatch):
    """Old PC writes metadata.json into the archive; new PC starts with an empty database
    pointed at the same archive folder and restores from it."""
    metadata.write(config.paths.archive, metadata.dump(session, db.revision_of(session)))

    new_home = tmp_path / "new-pc"
    monkeypatch.setenv("TABULARIUM_HOME", str(new_home))
    url = db.sqlite_url(tmp_path / "new-pc.db")
    db.upgrade(url)
    engine = db.make_engine(url)
    factory = db.make_session_factory(engine)
    app = create_app(config, csrf_token=TOKEN, session_factory=factory, scan_on_startup=False)
    new_pc = TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})

    assert "Restore from metadata.json" in new_pc.get("/maintenance").text
    r = new_pc.post("/maintenance/restore")
    assert "Database restored from metadata.json." in r.text
    assert "All good: 2 of 2 documents verified." in r.text
    r = new_pc.get("/search", params={"q": "zeznanie"})
    assert "1 document(s) found." in r.text
    with factory() as s:
        assert s.scalars(select(AuditLog.action).order_by(AuditLog.id.desc())).first() == "RESTORE"
    engine.dispose()


def _new_pc_app(config, tmp_path, *, scan_on_startup=True):
    url = db.sqlite_url(tmp_path / "new-pc.db")
    db.upgrade(url)
    engine = db.make_engine(url)
    factory = db.make_session_factory(engine)
    app = create_app(
        config, csrf_token=TOKEN, session_factory=factory, scan_on_startup=scan_on_startup
    )
    return app, factory, engine


def test_empty_database_never_overwrites_the_archive_metadata(session, rich, config, tmp_path):
    """New PC / lost database: the archive's metadata.json is the only restore source."""
    original = metadata.write(config.paths.archive, metadata.dump(session, db.revision_of(session)))
    before = original.read_bytes()
    (config.paths.ingest / "waiting.pdf").write_bytes(b"%PDF new")  # must not be ingested yet
    config.backup.metadata_interval_seconds = 5

    app, factory, engine = _new_pc_app(config, tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN}) as c:
        app.state.metadata_exporter.interval = 0.05  # let the background loop run
        import time

        time.sleep(0.5)
        home = c.get("/").text
        assert "Restore" in home and "/maintenance" in home  # banner
        r = c.post("/ingest/scan")
        assert "restore" in r.text.lower()
        r = c.post("/maintenance/metadata")
        assert "not overwriting" in r.text.lower()
    assert original.read_bytes() == before  # untouched, also by the shutdown write
    assert (config.paths.ingest / "waiting.pdf").exists()
    engine.dispose()


def test_after_restore_metadata_is_written_again(session, rich, config, tmp_path):
    metadata.write(config.paths.archive, metadata.dump(session, db.revision_of(session)))
    app, factory, engine = _new_pc_app(config, tmp_path, scan_on_startup=False)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN}) as c:
        assert "Database restored" in c.post("/maintenance/restore").text
    data = metadata.read(config.paths.archive / "metadata.json")
    assert data["counts"]["document"] == 2
    assert data["tables"]["audit_log"][-1]["action"] == "RESTORE"
    engine.dispose()
