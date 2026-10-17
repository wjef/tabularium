"""FastAPI application factory."""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tabularium import __version__, db
from tabularium.config import Config
from tabularium.ingest import IngestBusyError, Ingestor
from tabularium.services.documents import untagged_documents
from tabularium.storage import LocalStorage
from tabularium.web import routes_documents, routes_entities, routes_history, routes_tags
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
) -> FastAPI:
    """Build the app. The database must already be migrated (see ``__main__``)."""
    csrf_token = csrf_token or new_csrf_token()
    if session_factory is None:
        engine = db.make_engine(db.sqlite_url(config.database_path))
        session_factory = db.make_session_factory(engine)
    storage = LocalStorage(config.paths.archive)
    ingestor = Ingestor(config, session_factory, storage)
    if scan_on_startup is None:
        scan_on_startup = config.ingest.scan_on_startup

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if scan_on_startup:
            threading.Thread(target=_startup_scan, args=(ingestor,), daemon=True).start()
        yield

    app = FastAPI(
        title="Tabularium", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.config = config
    app.state.csrf_token = csrf_token
    app.state.session_factory = session_factory
    app.state.ingestor = ingestor
    app.state.storage = storage
    app.state.templates = make_templates(config, csrf_token)
    app.add_middleware(LocalOnlyMiddleware, csrf_token=csrf_token)
    app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")
    app.include_router(routes_documents.router)
    app.include_router(routes_tags.router)
    app.include_router(routes_entities.router)
    app.include_router(routes_history.router)

    def untagged_count() -> int:
        with session_factory() as session:
            query = select(func.count()).select_from(untagged_documents().subquery())
            return session.scalar(query) or 0

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(request, "index.html", {"config": config, "untagged": untagged_count()})

    @app.post("/ingest/scan", response_class=HTMLResponse)
    async def ingest_scan(request: Request):
        try:
            report = await run_in_threadpool(ingestor.scan)
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
