import hashlib
import io
import os

import pytest

from tabularium.storage import LocalStorage, StorageError, storage_key

DATA = b"%PDF-1.4 example"
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "Archive")


def test_storage_key_is_sharded_and_normalizes_extension():
    assert storage_key(SHA, ".PDF") == f"{SHA[:2]}/{SHA}.pdf"
    assert storage_key(SHA, "") == f"{SHA[:2]}/{SHA}"
    assert storage_key(SHA, ".we ird") == f"{SHA[:2]}/{SHA}"


def test_stage_and_commit_store_identical_bytes(storage):
    staged = storage.stage(io.BytesIO(DATA))
    assert (staged.sha256, staged.size) == (SHA, len(DATA))
    key = storage_key(SHA, ".pdf")
    assert storage.commit(staged, key) is True
    assert not staged.temp_path.exists()
    with storage.open(key) as f:
        assert f.read() == DATA


def test_commit_reuses_identical_existing_file(storage):
    key = storage_key(SHA, ".pdf")
    storage.commit(storage.stage(io.BytesIO(DATA)), key)
    again = storage.stage(io.BytesIO(DATA))
    assert storage.commit(again, key) is False
    assert not again.temp_path.exists()


def test_commit_refuses_to_overwrite_different_content(storage):
    key = storage_key(SHA, ".pdf")
    storage.commit(storage.stage(io.BytesIO(DATA)), key)
    other = storage.stage(io.BytesIO(b"different"))
    with pytest.raises(StorageError):
        storage.commit(other, key)


def test_cleanup_removes_only_old_staged_copies(storage):
    import time

    old = storage.stage(io.BytesIO(b"old"))
    fresh = storage.stage(io.BytesIO(b"fresh"))
    day_ago = time.time() - 2 * 86400
    os.utime(old.temp_path, (day_ago, day_ago))
    assert storage.cleanup_staging(older_than=86400, now=time.time()) == 1
    assert not old.temp_path.exists()
    assert fresh.temp_path.exists()


@pytest.mark.parametrize("key", ["../evil.pdf", "/abs.pdf", "ab/../../x"])
def test_path_traversal_rejected(storage, key):
    with pytest.raises(StorageError):
        storage.exists(key)
