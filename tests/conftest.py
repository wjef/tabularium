from pathlib import Path

import pytest

from tabularium.config import Config, load_config


@pytest.fixture(autouse=True)
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own app home — never the real one."""
    home = tmp_path / "home"
    monkeypatch.setenv("TABULARIUM_HOME", str(home))
    monkeypatch.delenv("TABULARIUM_DENYLIST", raising=False)
    return home


@pytest.fixture
def folders(tmp_path: Path) -> dict[str, Path]:
    result = {}
    for name in ("ingest", "archive", "outdated"):
        folder = tmp_path / "Tabularium" / name.capitalize()
        folder.mkdir(parents=True)
        result[name] = folder
    return result


def write_config(path: Path, folders: dict[str, Path], extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[paths]\n"
        + "".join(f'{name} = "{folder.as_posix()}"\n' for name, folder in folders.items())
        + extra,
        encoding="utf-8",
    )
    return path


@pytest.fixture
def config(app_home: Path, folders: dict[str, Path]) -> Config:
    return load_config(write_config(app_home / "tabularium.toml", folders))
