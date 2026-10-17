import tomllib
from pathlib import Path

import pytest
from conftest import write_config

from tabularium import paths
from tabularium.config import ConfigError, example_config_text, load_config


def test_first_run_creates_config_from_example(app_home: Path):
    with pytest.raises(ConfigError, match="Created a new configuration file"):
        load_config()
    assert paths.config_file().read_text(encoding="utf-8") == example_config_text()


def test_example_config_is_valid_toml_with_placeholder_paths():
    raw = tomllib.loads(example_config_text())
    assert set(raw["paths"]) == {"ingest", "archive", "outdated"}
    assert all(value.startswith("D:/Example/") for value in raw["paths"].values())


def test_valid_config_loads_with_defaults(config):
    assert config.server.host == "127.0.0.1"
    assert config.retention.years == 5
    assert config.ui.language == "en"
    assert config.database_path == paths.data_dir() / "tabularium.db"
    assert config.outdated_database_path.name == "outdated.db"


def test_missing_folders_are_reported(app_home: Path, folders):
    folders["archive"] = folders["archive"].parent / "does-not-exist"
    path = write_config(app_home / "tabularium.toml", folders)
    with pytest.raises(ConfigError, match="paths.archive"):
        load_config(path)


def test_folders_must_not_be_nested(app_home: Path, folders):
    nested = folders["archive"] / "inner"
    nested.mkdir()
    folders["ingest"] = nested
    path = write_config(app_home / "tabularium.toml", folders)
    with pytest.raises(ConfigError, match="must not be inside"):
        load_config(path)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])  # noqa: S104
def test_non_loopback_host_is_rejected(app_home: Path, folders, host: str):
    path = write_config(app_home / "tabularium.toml", folders, f'[server]\nhost = "{host}"\n')
    with pytest.raises(ConfigError, match="loopback"):
        load_config(path)


def test_unknown_keys_are_rejected(app_home: Path, folders):
    path = write_config(app_home / "tabularium.toml", folders, "[server]\nprot = 1234\n")
    with pytest.raises(ConfigError, match="server.prot"):
        load_config(path)


def test_invalid_toml_is_reported(app_home: Path):
    path = app_home / "tabularium.toml"
    path.parent.mkdir(parents=True)
    path.write_text("[paths\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)
