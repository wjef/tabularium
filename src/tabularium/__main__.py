"""Entry point: ``python -m tabularium``."""

import argparse
import logging
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler

import uvicorn

from tabularium import __version__, db, paths
from tabularium.backup import backup_now
from tabularium.config import ConfigError, load_config
from tabularium.lock import AlreadyRunningError, InstanceLock
from tabularium.web.app import create_app

log = logging.getLogger("tabularium")


def setup_logging() -> None:
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        paths.logs_dir() / "tabularium.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler, logging.StreamHandler()],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tabularium")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--version", action="version", version=f"tabularium {__version__}")
    args = parser.parse_args(argv)

    setup_logging()
    log.info("Tabularium %s, app home: %s", __version__, paths.app_home())

    try:
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        with InstanceLock(paths.lock_file()):
            config.database_path.parent.mkdir(parents=True, exist_ok=True)
            backup = db.upgrade_with_backup(config.database_path, paths.backups_dir())
            if backup:
                log.info("Database upgraded; previous version saved as %s", backup)
            backup_now(config.database_path, paths.backups_dir(), config.backup.keep)

            url = f"http://{config.server.host}:{config.server.port}/"
            if config.server.open_browser and not args.no_browser:
                threading.Timer(1.0, webbrowser.open, args=(url,)).start()
            log.info("Serving on %s", url)
            uvicorn.run(
                create_app(config),
                host=config.server.host,
                port=config.server.port,
                log_config=None,
            )
    except AlreadyRunningError as exc:
        print(exc, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
