import csv
import hashlib
import io
import os
import stat
import uuid
import zipfile
from datetime import date

import pytest
from conftest import etype
from fastapi.testclient import TestClient

from tabularium import desktop
from tabularium.export import readable_name, unique_names
from tabularium.models import Document
from tabularium.services.documents import (
    add_tag,
    create_document,
    set_document_date,
    update_details,
)
from tabularium.services.entities import create_entity
from tabularium.services.tags import create_tag
from tabularium.storage import LocalStorage, storage_key
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


@pytest.fixture
def world(config, session_factory):
    storage = LocalStorage(config.paths.archive)
    ids = {}
    with session_factory() as s, s.begin():
        anna = create_entity(s, etype(s, "Person"), "Anna Nowak")
        sender = create_tag(s, "sender")
        s_anna = create_tag(s, parent=sender, entity=anna)
        category = create_tag(s, "category")
        taxes = create_tag(s, "taxes", parent=category)
        for key, name, data, title, when, tags in [
            ("pit", "pit.pdf", b"%PDF pit", "Zeznanie PIT", date(2025, 4, 1), [s_anna, taxes]),
            ("bill", "bill.pdf", b"%PDF bill", None, date(2024, 1, 15), [s_anna]),
            ("other", "other.pdf", b"%PDF other", None, None, []),
        ]:
            staged = storage.stage(io.BytesIO(data))
            key_ = storage_key(staged.sha256, ".pdf")
            storage.commit(staged, key_)
            d = create_document(
                s,
                sha256=staged.sha256,
                stored_path=key_,
                original_filename=name,
                size_bytes=len(data),
                mime_type="application/pdf",
                retention_years=5,
            )
            if title:
                update_details(s, d, title=title)
            if when:
                set_document_date(s, d, when, retention_years=5)
            for t in tags:
                add_tag(s, d, t)
            ids[key] = str(d.id)
        ids |= {"anna": str(anna.id), "sender": str(sender.id), "taxes": str(taxes.id)}
    return ids


def result_names(html: str) -> list[str]:
    import re

    return re.findall(r'id=[0-9a-f-]{36}">([^<]+)</a>', html)


# --- search page -----------------------------------------------------------------------


BILL = hashlib.sha256(b"%PDF bill").hexdigest() + ".pdf"
OTHER = hashlib.sha256(b"%PDF other").hexdigest() + ".pdf"


def test_search_page_filters_by_tags_entities_text_dates(client, world):
    # documents without a title are listed by their archive (hash) name
    assert result_names(client.get("/search").text) == ["Zeznanie PIT", BILL, OTHER]
    r = client.get("/search", params={"tag": world["taxes"]})
    assert result_names(r.text) == ["Zeznanie PIT"]
    r = client.get("/search", params={"tag": world["sender"]})
    assert result_names(r.text) == ["Zeznanie PIT", BILL]
    r = client.get("/search", params={"entity": world["anna"], "from": "2025-01-01"})
    assert result_names(r.text) == ["Zeznanie PIT"]
    r = client.get("/search", params={"q": "zeznanie"})
    assert result_names(r.text) == ["Zeznanie PIT"]
    assert "1 document(s) found." in r.text
    r = client.get("/search", params={"q": BILL[:10]})  # by hash prefix
    assert result_names(r.text) == [BILL]
    r = client.get("/search", params={"q": "bill.pdf"})  # original name still searchable
    assert result_names(r.text) == [BILL]


def test_selected_result_keeps_filters_and_shows_preview(client, world):
    r = client.get("/search", params={"tag": world["sender"], "id": world["bill"]})
    assert f'<iframe src="/documents/{world["bill"]}/file"' in r.text
    assert f'href="/search?tag={world["sender"]}&amp;id={world["pit"]}"' in r.text
    assert "checked" in r.text  # selected tag stays ticked


def test_bad_filter_values_are_ignored(client, world):
    r = client.get("/search", params={"tag": "not-a-uuid", "from": "yesterday"})
    assert r.status_code == 200
    assert len(result_names(r.text)) == 3


# --- export ----------------------------------------------------------------------------


def test_readable_names_use_title_else_hash_and_are_safe():
    sha = "ab" * 32

    def doc(title, name="scan_0001.PDF", when=None):
        return Document(title=title, original_filename=name, sha256=sha, document_date=when)

    assert readable_name(doc("Faktura 03/2025: prąd?", when=date(2025, 3, 1))) == (
        "2025-03-01 Faktura 03_2025_ prąd_.pdf"
    )
    # no title: the hash, never the (random) original name
    assert readable_name(doc(None)) == f"{sha}.pdf"
    assert readable_name(doc(None, when=date(2024, 1, 15))) == f"2024-01-15 {sha}.pdf"
    assert readable_name(doc("CON", "x.txt")) == "_CON.txt"
    assert readable_name(doc("...", "a.pdf")) == "document.pdf"
    names = [n for _, n in unique_names([doc("Umowa"), doc("umowa"), doc("Umowa")])]
    assert names == ["Umowa.pdf", "umowa (2).pdf", "Umowa (3).pdf"]


def test_export_zip_has_readable_copies_and_index(client, world):
    r = client.post("/documents/export", data={"doc_ids": [world["pit"], world["bill"]]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="Tabularium export')
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == [
            f"2024-01-15 {BILL}",
            "2025-04-01 Zeznanie PIT.pdf",
            "index.csv",
        ]
        assert z.read("2025-04-01 Zeznanie PIT.pdf") == b"%PDF pit"
        index = z.read("index.csv").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(index), delimiter=";"))
    pit = next(row for row in rows if row["original_filename"] == "pit.pdf")
    assert pit["tags"] == "category / taxes | sender / Anna Nowak"
    assert pit["sha256"] == hashlib.sha256(b"%PDF pit").hexdigest()


def test_export_requires_selection_and_csrf(client, config, session_factory, world):
    assert client.post("/documents/export", data={}).status_code == 400
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    assert plain.post("/documents/export", data={"doc_ids": [world["pit"]]}).status_code == 403


def test_export_does_not_touch_the_archive(client, world, config):
    before = {p: p.read_bytes() for p in config.paths.archive.rglob("*.pdf")}
    client.post("/documents/export", data={"doc_ids": [world["pit"]]})
    assert {p: p.read_bytes() for p in config.paths.archive.rglob("*.pdf")} == before


# --- open copy / show in explorer --------------------------------------------------------


def test_open_hands_a_read_only_readable_copy_to_windows(client, world, monkeypatch, app_home):
    opened = []
    monkeypatch.setattr(desktop, "open_with_default_app", opened.append)
    r = client.post(f"/documents/{world['pit']}/open")
    assert "read-only copy" in r.text
    [copy] = opened
    assert copy.name == "2025-04-01 Zeznanie PIT.pdf"
    assert copy.read_bytes() == b"%PDF pit"
    assert app_home in copy.parents
    assert not os.stat(copy).st_mode & stat.S_IWRITE


def test_reveal_points_at_the_archived_file(client, world, monkeypatch, config):
    shown = []
    monkeypatch.setattr(desktop, "show_in_explorer", shown.append)
    client.post(f"/documents/{world['bill']}/reveal")
    [path] = shown
    sha = hashlib.sha256(b"%PDF bill").hexdigest()
    assert path == config.paths.archive / sha[:2] / f"{sha}.pdf"


def test_desktop_errors_are_shown(client, world, monkeypatch):
    def fail(path):
        raise desktop.DesktopError("Opening files is only supported on Windows.")

    monkeypatch.setattr(desktop, "open_with_default_app", fail)
    r = client.post(f"/documents/{world['pit']}/open")
    assert 'class="flash error"' in r.text


def test_unknown_document_for_desktop_actions(client):
    assert client.post(f"/documents/{uuid.uuid4()}/open").status_code == 404


@pytest.mark.parametrize(
    "name", ["setup.exe", "run.BAT", "script.ps1", "page.hta", "x.lnk", "a.js"]
)
def test_programs_and_scripts_are_never_opened(client, config, session_factory, monkeypatch, name):
    opened = []
    monkeypatch.setattr(desktop, "open_with_default_app", opened.append)
    storage = LocalStorage(config.paths.archive)
    staged = storage.stage(io.BytesIO(name.encode()))
    key = storage_key(staged.sha256, os.path.splitext(name)[1])
    storage.commit(staged, key)
    with session_factory() as s, s.begin():
        doc_id = create_document(
            s,
            sha256=staged.sha256,
            stored_path=key,
            original_filename=name,
            size_bytes=staged.size,
            retention_years=5,
        ).id
    r = client.post(f"/documents/{doc_id}/open")
    assert "not opened from the archive" in r.text
    assert opened == []
