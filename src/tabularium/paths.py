"""Location of the app home: config, databases, backups, logs (see docs/adr/0002)."""

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Tabularium"
HOME_ENV = "TABULARIUM_HOME"


def app_home() -> Path:
    """Return the app home directory, ``%LOCALAPPDATA%\\Tabularium`` unless overridden."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    return Path(user_data_dir(APP_NAME, appauthor=False, roaming=False))


def config_file() -> Path:
    return app_home() / "tabularium.toml"


def data_dir() -> Path:
    return app_home() / "data"


def logs_dir() -> Path:
    return app_home() / "logs"


def backups_dir() -> Path:
    return app_home() / "backups"


def lock_file() -> Path:
    return app_home() / "tabularium.lock"


def denylist_file() -> Path:
    return app_home() / "denylist.txt"
