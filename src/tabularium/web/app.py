"""FastAPI application factory."""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from tabularium import __version__, db
from tabularium.config import Config
from tabularium.evernote.convert import EdgePrinter
from tabularium.evernote.importer import EvernoteImporter, ImportJob
from tabularium.ingest import IngestBusyError, Ingestor
from tabularium.metadata import MetadataExporter, restore_pending
from tabularium.outdated import OutdatedStore
from tabularium.storage import LocalStorage
from tabularium.textextract import TesseractOcr, tesseract_status
from tabularium.textindex import TextIndexer
from tabularium.web import api
from tabularium.web.security import LocalOnlyMiddleware, new_csrf_token

log = logging.getLogger(__name__)
_WEB = resources.files("tabularium") / "web"


def create_app(
    config: Config,
    *,
    csrf_token: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    scan_on_startup: bool | None = None,
    outdated_store: OutdatedStore | None = None,
) -> FastAPI:
    """Build the app. The database must already be migrated (see ``__main__``)."""
    csrf_token = csrf_token or new_csrf_token()
    if session_factory is None:
        engine = db.make_engine(db.sqlite_url(config.database_path))
        session_factory = db.make_session_factory(engine)
    storage = LocalStorage(config.paths.archive)
    if outdated_store is None:
        outdated_store = OutdatedStore(config.outdated_database_path, config.paths.outdated)
    ingestor = Ingestor(config, session_factory, storage, is_outdated=outdated_store.has_hash)
    text_indexer = TextIndexer(
        session_factory,
        storage,
        ocr_factory=lambda: _ocr_engine(config),
        dpi=config.text.ocr_dpi,
        max_pages=config.text.max_pages,
    )
    exporter = MetadataExporter(
        session_factory,
        config.paths.archive,
        schema_revision=lambda: _revision(session_factory),
        interval=config.backup.metadata_interval_seconds,
    ).attach()
    if scan_on_startup is None:
        scan_on_startup = config.ingest.scan_on_startup

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        exporter.start()
        if config.text.enabled:
            text_indexer.start()
        if scan_on_startup and not is_restore_pending():
            threading.Thread(target=_startup_scan, args=(ingestor,), daemon=True).start()
        yield
        text_indexer.stop()
        exporter.stop()

    app = FastAPI(
        title="Tabularium", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.config = config
    app.state.csrf_token = csrf_token
    app.state.session_factory = session_factory
    app.state.ingestor = ingestor
    app.state.evernote = ImportJob(
        EvernoteImporter(ingestor, EdgePrinter()), on_done=text_indexer.wake
    )
    app.state.storage = storage
    app.state.metadata_exporter = exporter
    app.state.outdated_store = outdated_store
    app.state.text_indexer = text_indexer
    app.add_middleware(LocalOnlyMiddleware, csrf_token=csrf_token)
    api.install(app)

    def is_restore_pending() -> bool:
        with session_factory() as session:
            return restore_pending(session, config.paths.archive)

    app.state.restore_pending = is_restore_pending

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    _install_spa(app)  # last: its catch-all route must not hide the API
    return app


def _startup_scan(ingestor: Ingestor) -> None:
    try:
        ingestor.scan()
    except IngestBusyError:
        pass
    except Exception:  # noqa: BLE001 — a failed scan must not stop the app
        log.exception("Startup ingest scan failed")


def _revision(session_factory: sessionmaker[Session]) -> str | None:
    with session_factory() as session:
        return db.revision_of(session)


def _ocr_engine(config: Config):
    """Tesseract if OCR is enabled and installed with the configured languages."""
    if not (config.text.enabled and config.text.ocr):
        return None
    status = tesseract_status(config.text.tesseract_cmd, config.text.languages)
    if not status.available:
        log.info("OCR not available: %s", status.message)
        return None
    return TesseractOcr(status.command, config.text.languages)


SPA_DIR = _WEB / "spa"  # built by `npm run build` in frontend/ (not committed)


def _install_spa(app: FastAPI) -> None:
    """Serve the single-page front end; every path that is not API is one of its screens."""
    assets = SPA_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
    def spa(path: str = ""):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # top-level build files (favicon.svg); everything else is a client-side route
        if path and "/" not in path and "\\" not in path and not path.startswith("."):
            file = SPA_DIR / path
            if file.is_file() and path != "index.html":
                return FileResponse(file)
        index = SPA_DIR / "index.html"
        if not index.is_file():
            return HTMLResponse(
                "<p>The interface is not built yet. Run <code>scripts/setup-dev.ps1</code>"
                " (needs Node.js), then reload.</p>",
                status_code=503,
            )
        return HTMLResponse(
            index.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"}
        )
