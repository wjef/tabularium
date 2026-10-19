"""JSON API for the single-page front end: ``/api/v1``.

Same protections as the rest of the app (loopback Host, Origin check, CSRF header on every
change). Domain rule violations come back as ``409 {"detail": "<message>"}``.
"""

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from tabularium.services import DomainError
from tabularium.web.api import catalog, documents, manage

router = APIRouter(prefix="/api/v1")
router.include_router(catalog.router)
router.include_router(documents.router)
router.include_router(manage.router)


async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(DomainError, _domain_error)
