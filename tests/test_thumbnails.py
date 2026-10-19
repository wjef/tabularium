"""Thumbnails: first PDF page or the photo, cached by content hash, none for other types."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_fulltext import make_pdf

from tabularium.services.documents import create_document
from tabularium.storage import LocalStorage, storage_key
from tabularium.thumbnails import WIDTH, thumbnail_path
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER


def png(size=(800, 400)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "teal").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def add(config, session_factory):
    storage = LocalStorage(config.paths.archive)

    def add(content: bytes, mime: str, ext: str):
        staged = storage.stage(io.BytesIO(content))
        key = storage_key(staged.sha256, ext)
        storage.commit(staged, key)
        with session_factory() as s, s.begin():
            document = create_document(
                s,
                sha256=staged.sha256,
                stored_path=key,
                original_filename=f"file{ext}",
                size_bytes=len(content),
                mime_type=mime,
                retention_years=5,
            )
            s.expunge(document)
            return document

    return storage, add


def test_pdf_and_image_thumbnails_are_made_once_and_cached(add, tmp_path):
    storage, make = add
    cache = tmp_path / "thumbs"
    pdf = make(make_pdf(["Invoice FV 03/2025"]), "application/pdf", ".pdf")
    photo = make(png(), "image/png", ".png")

    first = thumbnail_path(storage, pdf, cache)
    assert first is not None and first.parent == cache
    with Image.open(first) as image:
        assert image.format == "WEBP" and image.width == WIDTH
    mtime = first.stat().st_mtime_ns
    assert thumbnail_path(storage, pdf, cache) == first and first.stat().st_mtime_ns == mtime

    with Image.open(thumbnail_path(storage, photo, cache)) as image:
        assert image.size == (WIDTH, WIDTH // 2)  # aspect ratio kept
    assert not list(cache.glob("*.tmp"))


def test_other_types_and_broken_files_have_none(add, tmp_path):
    storage, make = add
    text = make(b"plain note", "text/plain", ".txt")
    broken = make(b"%PDF-1.4 garbage", "application/pdf", ".pdf")
    assert thumbnail_path(storage, text, tmp_path) is None
    assert thumbnail_path(storage, broken, tmp_path) is None


def test_thumbnail_endpoint(add, config, session_factory):
    _, make = add
    pdf = make(make_pdf(["Contract"]), "application/pdf", ".pdf")
    text = make(b"plain note", "text/plain", ".txt")
    app = create_app(config, csrf_token="t", session_factory=session_factory, scan_on_startup=False)
    client = TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: "t"})

    response = client.get(f"/api/v1/documents/{pdf.id}/thumbnail")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert "max-age" in response.headers["cache-control"]
    assert client.get(f"/api/v1/documents/{text.id}/thumbnail").status_code == 404
