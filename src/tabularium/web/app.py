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
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tabularium import __version__, db
from tabularium.config import Config
from tabularium.i18n import get_translations
from tabularium.ingest import IngestBusyError, Ingestor
from tabularium.services.documents import untagged_documents
from tabularium.storage import LocalStorage
from tabularium.web.security import CSRF_HEADER, LocalOnlyMiddleware, new_csrf_token

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
    ingestor = Ingestor(config, session_factory, LocalStorage(config.paths.archive))
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
    app.add_middleware(LocalOnlyMiddleware, csrf_token=csrf_token)
    app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")

    env = Environment(
        loader=FileSystemLoader(str(_WEB / "templates")),
        autoescape=select_autoescape(),
        extensions=["jinja2.ext.i18n"],
    )
    templates = Jinja2Templates(env=env)
    templates.env.install_gettext_translations(get_translations(config.ui.language), newstyle=True)
    templates.env.globals.update(
        csrf_header=CSRF_HEADER,
        csrf_token=csrf_token,
        version=__version__,
        language=config.ui.language,
    )

    def untagged_count() -> int:
        with session_factory() as session:
            query = select(func.count()).select_from(untagged_documents().subquery())
            return session.scalar(query) or 0

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request, "index.html", {"config": config, "untagged": untagged_count()}
        )

    @app.post("/ingest/scan", response_class=HTMLResponse)
    async def ingest_scan(request: Request):
        try:
            report = await run_in_threadpool(ingestor.scan)
        except IngestBusyError:
            report = None
        return templates.TemplateResponse(
            request,
            "_ingest_report.html",
            {"report": report, "untagged": untagged_count()},
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
