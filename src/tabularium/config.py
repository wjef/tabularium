"""Loading and validation of ``tabularium.toml``."""

import ipaddress
import tomllib
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tabularium import paths


class ConfigError(Exception):
    """Configuration is missing or invalid; the message is meant for the user."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(_Section):
    ingest: Path
    archive: Path
    outdated: Path
    database: Path | None = None


class ServerConfig(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    open_browser: bool = True

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value == "localhost":
            return value
        try:
            is_loopback = ipaddress.ip_address(value).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError("must be a loopback address (127.0.0.1 or localhost)")
        return value


class IngestConfig(_Section):
    scan_on_startup: bool = True
    include_subfolders: bool = True
    after_import: Literal["move_to_processed"] = "move_to_processed"
    stable_seconds: int = Field(default=5, ge=0)
    ignore: list[str] = Field(default_factory=list)


class RetentionConfig(_Section):
    years: int = Field(default=5, ge=0)


class BackupConfig(_Section):
    keep: int = Field(default=10, ge=1)  # database backups made at startup
    metadata_interval_seconds: int = Field(default=30, ge=5)  # metadata.json refresh


class TextConfig(_Section):
    enabled: bool = True  # extract text for full-text search
    ocr: bool = True  # read scans and photos with Tesseract when it is installed
    tesseract_cmd: str = ""  # empty: find tesseract.exe automatically
    languages: str = "pol+eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    max_pages: int = Field(default=300, ge=1)


class UiConfig(_Section):
    language: str = "en"


class Config(_Section):
    paths: PathsConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    ui: UiConfig = Field(default_factory=UiConfig)

    @property
    def database_path(self) -> Path:
        return self.paths.database or paths.data_dir() / "tabularium.db"

    @property
    def outdated_database_path(self) -> Path:
        return self.database_path.with_name("outdated.db")


def example_config_text() -> str:
    example = resources.files("tabularium.resources").joinpath("tabularium.example.toml")
    return example.read_text(encoding="utf-8")


def create_default_config(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(example_config_text(), encoding="utf-8")


def load_config(path: Path | None = None, *, check_folders: bool = True) -> Config:
    """Load and validate the config. Creates it from the example on first run."""
    path = path or paths.config_file()
    if not path.exists():
        create_default_config(path)
        raise ConfigError(
            f"Created a new configuration file:\n  {path}\n"
            "Edit the [paths] section (ingest, archive, outdated) and start Tabularium again."
        )

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        details = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"{path}: invalid configuration:\n{details}") from exc

    if check_folders:
        _check_folders(config, path)
    return config


def _check_folders(config: Config, path: Path) -> None:
    folders = {
        "ingest": config.paths.ingest,
        "archive": config.paths.archive,
        "outdated": config.paths.outdated,
    }
    missing = [
        f"  paths.{name} = {folder}" for name, folder in folders.items() if not folder.is_dir()
    ]
    if missing:
        raise ConfigError(
            f"{path}: these folders do not exist:\n"
            + "\n".join(missing)
            + "\nCreate them or fix the paths."
        )

    resolved = {name: folder.resolve() for name, folder in folders.items()}
    if len(set(resolved.values())) != len(resolved):
        raise ConfigError(f"{path}: ingest, archive and outdated must be different folders.")
    for name, folder in resolved.items():
        for other_name, other in resolved.items():
            if name != other_name and folder.is_relative_to(other):
                raise ConfigError(f"{path}: paths.{name} must not be inside paths.{other_name}.")
