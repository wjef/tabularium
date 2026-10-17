"""FastAPI application factory."""

from importlib import resources

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tabularium import __version__
from tabularium.config import Config
from tabularium.i18n import get_translations
from tabularium.web.security import CSRF_HEADER, LocalOnlyMiddleware, new_csrf_token

_WEB = resources.files("tabularium") / "web"


def create_app(config: Config, *, csrf_token: str | None = None) -> FastAPI:
    csrf_token = csrf_token or new_csrf_token()

    app = FastAPI(title="Tabularium", version=__version__, docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.csrf_token = csrf_token
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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"config": config})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
