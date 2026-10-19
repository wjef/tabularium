"""FastAPI application factory."""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tabularium import __version__, db
from tabularium.config import Config
from tabularium.ingest import IngestBusyError, Ingestor
from tabularium.metadata import MetadataExporter, restore_pending
from tabularium.outdated import OutdatedStore
from tabularium.services.documents import untagged_documents
from tabularium.storage import LocalStorage
from tabularium.textextract import TesseractOcr, tesseract_status
from tabularium.textindex import TextIndexer
from tabularium.web import (
    api,
    routes_documents,
    routes_entities,
    routes_history,
    routes_maintenance,
    routes_outdated,
    routes_search,
    routes_tags,
)
from tabularium.web.deps import make_templates, render
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
    app.state.storage = storage
    app.state.metadata_exporter = exporter
    app.state.outdated_store = outdated_store
    app.state.text_indexer = text_indexer
    app.state.templates = make_templates(config, csrf_token)
    app.add_middleware(LocalOnlyMiddleware, csrf_token=csrf_token)
    app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")
    _install_spa(app)
    api.install(app)
    app.include_router(routes_documents.router)
    app.include_router(routes_outdated.router)
    app.include_router(routes_search.router)
    app.include_router(routes_tags.router)
    app.include_router(routes_entities.router)
    app.include_router(routes_history.router)
    app.include_router(routes_maintenance.router)

    def is_restore_pending() -> bool:
        with session_factory() as session:
            return restore_pending(session, config.paths.archive)

    app.state.restore_pending = is_restore_pending

    def untagged_count() -> int:
        with session_factory() as session:
            query = select(func.count()).select_from(untagged_documents().subquery())
            return session.scalar(query) or 0

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(
            request,
            "index.html",
            {
                "config": config,
                "untagged": untagged_count(),
                "restore_pending": is_restore_pending(),
            },
        )

    @app.post("/ingest/scan", response_class=HTMLResponse)
    async def ingest_scan(request: Request):
        if is_restore_pending():
            return render(
                request,
                "_ingest_report.html",
                {"report": None, "blocked": True, "untagged": untagged_count()},
            )
        try:
            report = await run_in_threadpool(ingestor.scan)
            text_indexer.wake()
        except IngestBusyError:
            report = None
        return render(
            request, "_ingest_report.html", {"report": report, "untagged": untagged_count()}
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

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
    """Serve the single-page front end at /app (the old interface stays at /)."""
    assets = SPA_DIR / "assets"
    if assets.is_dir():
        app.mount("/app/assets", StaticFiles(directory=str(assets)), name="spa-assets")

    @app.get("/app", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/app/{path:path}", response_class=HTMLResponse, include_in_schema=False)
    def spa(path: str = ""):
        # top-level build files (favicon.svg); everything else is a client-side route
        if path and "/" not in path and "\\" not in path and not path.startswith("."):
            file = SPA_DIR / path
            if file.is_file() and path != "index.html":
                return FileResponse(file)
        index = SPA_DIR / "index.html"
        if not index.is_file():
            return HTMLResponse(
                "<p>The new interface is not built yet. Run <code>scripts/setup-dev.ps1</code>"
                " (needs Node.js), then reload.</p>",
                status_code=503,
            )
        return HTMLResponse(index.read_text(encoding="utf-8"))
