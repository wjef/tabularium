import hashlib
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from tabularium import fsutil, ingest
from tabularium.fsutil import lp
from tabularium.ingest import IngestBusyError, Ingestor
from tabularium.models import Document, IngestLog, IngestResult
from tabularium.storage import LocalStorage, storage_key


@pytest.fixture
def ingestor(config, session_factory):
    config.ingest.stable_seconds = 0
    return Ingestor(config, session_factory, LocalStorage(config.paths.archive))


def put(folder: Path, relpath: str, data: bytes) -> Path:
    path = folder / relpath
    os.makedirs(lp(path.parent), exist_ok=True)
    with open(lp(path), "wb") as f:
        f.write(data)
    return path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def documents(session_factory) -> list[Document]:
    with session_factory() as s:
        return list(s.scalars(select(Document)))


def logs(session_factory) -> list[tuple[str, IngestResult]]:
    with session_factory() as s:
        return [(e.source_path, e.result) for e in s.scalars(select(IngestLog))]


def test_imports_file_unchanged_under_hash_name(ingestor, folders, session_factory):
    data = b"%PDF-1.4 invoice"
    put(folders["ingest"], "Urząd Skarbowy – PIT.pdf", data)

    report = ingestor.scan()

    assert report.imported == ["Urząd Skarbowy – PIT.pdf"]
    archived = folders["archive"] / sha(data)[:2] / f"{sha(data)}.pdf"
    assert archived.read_bytes() == data
    [doc] = documents(session_factory)
    assert doc.sha256 == sha(data)
    assert doc.stored_path == storage_key(sha(data), ".pdf")
    assert doc.original_filename == "Urząd Skarbowy – PIT.pdf"
    assert doc.original_relpath is None
    assert doc.mime_type == "application/pdf"
    assert doc.size_bytes == len(data)
    assert (folders["ingest"] / "_processed" / "Urząd Skarbowy – PIT.pdf").read_bytes() == data
    assert not (folders["ingest"] / "Urząd Skarbowy – PIT.pdf").exists()
    assert logs(session_factory) == [("Urząd Skarbowy – PIT.pdf", IngestResult.IMPORTED)]
    assert not list((folders["archive"] / ".tmp").iterdir())


def test_subfolders_are_kept_in_processed_and_recorded(ingestor, folders, session_factory):
    put(folders["ingest"], "phone/scans/receipt.jpg", b"jpeg-bytes")
    ingestor.scan()
    [doc] = documents(session_factory)
    assert doc.original_relpath == "phone/scans"
    assert (folders["ingest"] / "_processed" / "phone" / "scans" / "receipt.jpg").exists()


def test_include_subfolders_false(ingestor, folders, config):
    config.ingest.include_subfolders = False
    put(folders["ingest"], "sub/a.txt", b"a")
    put(folders["ingest"], "b.txt", b"b")
    assert ingestor.scan().imported == ["b.txt"]


def test_duplicates_are_moved_aside_not_archived_twice(ingestor, folders, session_factory):
    put(folders["ingest"], "a.pdf", b"same")
    put(folders["ingest"], "copy of a.pdf", b"same")
    report = ingestor.scan()
    assert report.imported == ["a.pdf"]
    assert report.duplicates == ["copy of a.pdf"]
    assert len(documents(session_factory)) == 1
    assert (folders["ingest"] / "_duplicates" / "copy of a.pdf").exists()
    put(folders["ingest"], "a.pdf", b"same")  # same name again, later
    assert ingestor.scan().duplicates == ["a.pdf"]
    assert (folders["ingest"] / "_duplicates" / "a.pdf").exists()


def test_name_collisions_in_processed_get_a_suffix(ingestor, folders):
    put(folders["ingest"], "scan.pdf", b"first")
    ingestor.scan()
    put(folders["ingest"], "scan.pdf", b"second")
    ingestor.scan()
    processed = folders["ingest"] / "_processed"
    assert (processed / "scan.pdf").read_bytes() == b"first"
    assert (processed / "scan (1).pdf").read_bytes() == b"second"


def test_ignored_and_reserved_entries_are_left_alone(ingestor, folders):
    put(folders["ingest"], "~$draft.docx", b"lock")
    put(folders["ingest"], "file.crdownload", b"partial")
    put(folders["ingest"], ".hidden", b"h")
    put(folders["ingest"], "_failed/old.pdf", b"old")
    put(folders["ingest"], "_processed/done.pdf", b"done")
    report = ingestor.scan()
    assert report.total == 0
    assert (folders["ingest"] / "~$draft.docx").exists()


def test_custom_ignore_patterns(ingestor, folders, config):
    config.ingest.ignore = ["*.bak"]
    put(folders["ingest"], "x.BAK", b"x")
    assert ingestor.scan().total == 0


def test_recent_files_wait_until_stable(config, session_factory, folders):
    config.ingest.stable_seconds = 60
    put(folders["ingest"], "fresh.pdf", b"fresh")
    clock = [time.time() + 1]  # just after the file was written
    ingestor = Ingestor(
        config, session_factory, LocalStorage(config.paths.archive), clock=lambda: clock[0]
    )
    report = ingestor.scan()
    assert report.waiting == ["fresh.pdf"] and report.imported == []
    clock[0] += 61
    assert ingestor.scan().imported == ["fresh.pdf"]


def test_future_timestamps_do_not_block_forever(config, session_factory, folders):
    config.ingest.stable_seconds = 60
    path = put(folders["ingest"], "from-the-future.pdf", b"x")
    future = time.time() + 3600
    os.utime(path, (future, future))
    ingestor = Ingestor(
        config,
        session_factory,
        LocalStorage(config.paths.archive),
        clock=lambda: time.time() + 120,  # birth time is old enough, mtime is in the future
    )
    assert ingestor.scan().imported == ["from-the-future.pdf"]


@pytest.mark.skipif(not fsutil.IS_WINDOWS, reason="file locking check is Windows-only")
def test_file_open_by_another_program_waits(ingestor, folders):
    path = put(folders["ingest"], "busy.pdf", b"busy")
    with open(path, "ab"):
        assert ingestor.scan().waiting == ["busy.pdf"]
    assert ingestor.scan().imported == ["busy.pdf"]


def test_file_changing_during_copy_waits(ingestor, folders, monkeypatch):
    put(folders["ingest"], "growing.pdf", b"part")
    real_stat = os.stat
    calls = {"n": 0}

    def stat_with_growth(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path).endswith("growing.pdf"):
            calls["n"] += 1
            if calls["n"] == 2:  # the re-check after copying sees a different size
                return os.stat_result((st.st_mode, *st[1:6], st.st_size + 10, *st[7:]))
        return st

    monkeypatch.setattr(ingest.os, "stat", stat_with_growth)
    report = ingestor.scan()
    assert report.waiting == ["growing.pdf"]
    assert report.imported == []
    assert (folders["ingest"] / "growing.pdf").exists()
    assert not list((folders["archive"] / ".tmp").iterdir())


def test_placeholders_are_skipped_and_logged(ingestor, folders, session_factory, monkeypatch):
    put(folders["ingest"], "cloud-only.pdf", b"x")
    monkeypatch.setattr(ingest, "is_placeholder", lambda st: True)
    report = ingestor.scan()
    assert report.skipped == ["cloud-only.pdf"]
    assert (folders["ingest"] / "cloud-only.pdf").exists()
    assert logs(session_factory) == [("cloud-only.pdf", IngestResult.SKIPPED)]


def test_database_failure_moves_original_to_failed_and_cleans_archive(
    ingestor, folders, session_factory, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ingest, "create_document", broken)
    put(folders["ingest"], "bad.pdf", b"bad")
    report = ingestor.scan()
    assert report.failed == ["bad.pdf"]
    assert (folders["ingest"] / "_failed" / "bad.pdf").read_bytes() == b"bad"
    assert documents(session_factory) == []
    assert not (folders["archive"] / sha(b"bad")[:2]).exists() or not any(
        (folders["archive"] / sha(b"bad")[:2]).iterdir()
    )
    with session_factory() as s:
        entry = s.scalars(select(IngestLog)).one()
    assert entry.result == IngestResult.FAILED
    assert "database unavailable" in entry.message


def test_leftover_archive_file_from_interrupted_run_is_reused(ingestor, folders, session_factory):
    data = b"interrupted"
    put(folders["archive"], storage_key(sha(data), ".pdf"), data)  # committed, never recorded
    put(folders["ingest"], "again.pdf", data)
    assert ingestor.scan().imported == ["again.pdf"]
    assert len(documents(session_factory)) == 1


def test_long_paths_are_supported(ingestor, folders, session_factory):
    deep = "/".join(["very-long-folder-name-" + str(i).zfill(3) for i in range(12)])
    relpath = f"{deep}/document with a long name.pdf"
    assert len(str(folders["ingest"] / relpath)) > 260
    put(folders["ingest"], relpath, b"deep")
    assert ingestor.scan().imported == [relpath]
    [doc] = documents(session_factory)
    assert doc.original_relpath == deep


def test_only_one_scan_at_a_time(ingestor):
    ingestor._lock.acquire()
    try:
        with pytest.raises(IngestBusyError):
            ingestor.scan()
    finally:
        ingestor._lock.release()


def test_retention_default_applied_on_import(ingestor, folders, session_factory, config):
    put(folders["ingest"], "x.pdf", b"x")
    ingestor.scan()
    [doc] = documents(session_factory)
    expected_year = doc.ingested_at.astimezone().year + config.retention.years
    assert doc.retain_until.year == expected_year
