"""Shared web helpers: templates, per-request database session."""

import json
import uuid
from collections.abc import Iterator
from importlib import resources
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from tabularium import __version__
from tabularium.config import Config
from tabularium.i18n import get_translations
from tabularium.web.security import CSRF_HEADER

_WEB = resources.files("tabularium") / "web"


def make_templates(config: Config, csrf_token: str) -> Jinja2Templates:
    env = Environment(
        loader=FileSystemLoader(str(_WEB / "templates")),
        autoescape=select_autoescape(),
        extensions=["jinja2.ext.i18n"],
    )
    # Readable JSON (keeps Polish letters); the result is still HTML-escaped by autoescape.
    env.filters["readable_json"] = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True)
    env.install_gettext_translations(get_translations(config.ui.language), newstyle=True)
    env.globals.update(
        csrf_header=CSRF_HEADER,
        csrf_token=csrf_token,
        version=__version__,
        language=config.ui.language,
    )
    return Jinja2Templates(env=env)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def render(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, template, context)


def parse_uuid(value: str | None) -> uuid.UUID | None:
    """UUID from a form or query value; None for empty or malformed input."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
