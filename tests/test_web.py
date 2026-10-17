import pytest
from fastapi.testclient import TestClient

from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )

    @app.post("/_test/mutate")
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_index_renders_with_csrf_header_for_htmx(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Tabularium" in response.text
    assert CSRF_HEADER in response.text and TOKEN in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "/static/htmx-2.0.11.min.js" in response.text
    assert client.get("/static/htmx-2.0.11.min.js").status_code == 200


def test_scan_button_imports_files(client, folders, config):
    config.ingest.stable_seconds = 0
    (folders["ingest"] / "letter.pdf").write_bytes(b"%PDF-1.4 letter")
    response = client.post("/ingest/scan", headers={CSRF_HEADER: TOKEN})
    assert response.status_code == 200
    assert "Imported" in response.text
    assert '<strong id="untagged-count">1</strong>' in response.text
    assert (folders["ingest"] / "_processed" / "letter.pdf").exists()


def test_scan_requires_csrf_token(client):
    assert client.post("/ingest/scan").status_code == 403


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.parametrize("host", ["evil.example.com", "attacker.localhost.example", "10.0.0.5"])
def test_foreign_host_header_is_rejected(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"])
def test_loopback_host_headers_are_accepted(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_post_without_csrf_token_is_rejected(client):
    assert client.post("/_test/mutate").status_code == 403


def test_post_with_wrong_csrf_token_is_rejected(client):
    assert client.post("/_test/mutate", headers={CSRF_HEADER: "wrong"}).status_code == 403


def test_post_from_foreign_origin_is_rejected(client):
    headers = {CSRF_HEADER: TOKEN, "Origin": "https://evil.example.com"}
    assert client.post("/_test/mutate", headers=headers).status_code == 403


def test_post_with_token_and_own_origin_is_accepted(client):
    headers = {CSRF_HEADER: TOKEN, "Origin": "http://127.0.0.1:8765"}
    response = client.post("/_test/mutate", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
