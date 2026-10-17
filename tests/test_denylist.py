import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_denylist.py"
_spec = importlib.util.spec_from_file_location("check_denylist", _SCRIPT)
check_denylist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_denylist)


@pytest.fixture
def denylist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "denylist.txt"
    path.write_text("# fictional test words\nKowalski\nzażółć  # comment\n\n", encoding="utf-8")
    monkeypatch.setenv("TABULARIUM_DENYLIST", str(path))
    return path


def test_normalize_folds_case_and_polish_diacritics():
    assert check_denylist.normalize("ZAŻÓŁĆ Gęślą") == "zazolc gesla"


def test_clean_file_passes(denylist, tmp_path: Path):
    clean = tmp_path / "clean.py"
    clean.write_text("print('hello')\n", encoding="utf-8")
    assert check_denylist.main([str(clean)]) == 0


def test_word_in_content_blocks_ignoring_case_and_diacritics(denylist, tmp_path: Path, capsys):
    bad = tmp_path / "notes.md"
    bad.write_text("line one\nsender: JAN KOWALSKI\nzazolc\n", encoding="utf-8")
    assert check_denylist.main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert "notes.md:2" in out and "notes.md:3" in out


def test_word_in_file_name_blocks_even_for_binary(denylist, tmp_path: Path):
    binary = tmp_path / "invoice_kowalski.bin"
    binary.write_bytes(b"\x00\x01\x02")
    assert check_denylist.main([str(binary)]) == 1


def test_commit_message_is_checked_without_git_comments(denylist, tmp_path: Path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("Fix import\n# kowalski in a git comment is ignored\n", encoding="utf-8")
    assert check_denylist.main(["--commit-msg", str(msg)]) == 0
    msg.write_text("Fix import for Kowalski\n", encoding="utf-8")
    assert check_denylist.main(["--commit-msg", str(msg)]) == 1


def test_denylist_with_utf8_bom_still_matches_first_word(tmp_path: Path, monkeypatch):
    path = tmp_path / "denylist.txt"
    path.write_text("Kowalski\n", encoding="utf-8-sig")
    monkeypatch.setenv("TABULARIUM_DENYLIST", str(path))
    bad = tmp_path / "x.txt"
    bad.write_text("kowalski", encoding="utf-8")
    assert check_denylist.main([str(bad)]) == 1


def test_missing_denylist_blocks(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TABULARIUM_DENYLIST", str(tmp_path / "missing.txt"))
    any_file = tmp_path / "x.txt"
    any_file.write_text("hello", encoding="utf-8")
    assert check_denylist.main([str(any_file)]) == 1
    assert "denylist not found" in capsys.readouterr().out


def test_explicit_opt_out_passes_with_warning(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TABULARIUM_DENYLIST", "none")
    any_file = tmp_path / "x.txt"
    any_file.write_text("Kowalski", encoding="utf-8")
    assert check_denylist.main([str(any_file)]) == 0
    assert "WARNING" in capsys.readouterr().out
