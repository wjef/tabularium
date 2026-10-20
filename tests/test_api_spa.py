"""Serving the single-page front end at / and the session endpoint it starts with."""

import pytest
from fastapi.testclient import TestClient

from tabularium.web import app as web_app
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def make_client(config, session_factory, monkeypatch, tmp_path):
    def make(built: bool):
        spa = tmp_path / "spa"
        if built:
            (spa / "assets").mkdir(parents=True)
            (spa / "index.html").write_text("<div id=root></div>", encoding="utf-8")
            (spa / "favicon.svg").write_text("<svg/>", encoding="utf-8")
            (spa / "assets" / "index.js").write_text("console.log(1)", encoding="utf-8")
        monkeypatch.setattr(web_app, "SPA_DIR", spa)
        app = create_app(
            config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
        )
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


def test_session_gives_the_csrf_header_and_token(make_client):
    body = make_client(built=False).get("/api/v1/session").json()
    assert body == {"csrf_header": CSRF_HEADER, "csrf_token": TOKEN}


def test_client_side_routes_get_the_index_page(make_client):
    client = make_client(built=True)
    for path in ("/", "/tags", "/entities", "/outdated", "/history", "/maintenance", "/x/y"):
        response = client.get(path)
        assert response.status_code == 200 and "root" in response.text
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/assets/index.js").text == "console.log(1)"


def test_build_files_cannot_be_escaped(make_client):
    client = make_client(built=True)
    for path in ("/..%2F..%2Fpyproject.toml", "/.hidden", "/a%5Cb", "/index.html/../x"):
        response = client.get(path)
        assert "root" in response.text  # falls back to the index page, never another file


def test_missing_build_explains_how_to_build(make_client):
    response = make_client(built=False).get("/")
    assert response.status_code == 503 and "setup-dev.ps1" in response.text
