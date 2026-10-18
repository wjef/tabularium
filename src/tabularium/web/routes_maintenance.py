"""Maintenance: integrity check, backups, metadata.json, restore (docs/requirements.md §10)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from tabularium import paths
from tabularium.backup import backup_now, list_backups
from tabularium.db import revision_of
from tabularium.i18n import N_
from tabularium.integrity import check
from tabularium.metadata import FILENAME, RestorePendingError, is_empty, read, restore
from tabularium.services import DomainError
from tabularium.textextract import tesseract_status
from tabularium.textindex import status_counts
from tabularium.web.deps import get_session, render

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def _context(request: Request, session: Session, **extra) -> dict:
    config = request.app.state.config
    metadata_file = config.paths.archive / FILENAME
    exporter = request.app.state.metadata_exporter
    return {
        "backups": list_backups(paths.backups_dir())[:20],
        "backup_dir": paths.backups_dir(),
        "keep": config.backup.keep,
        "metadata_file": metadata_file,
        "metadata_exists": metadata_file.exists(),
        "exporter": exporter,
        "can_restore": is_empty(session) and metadata_file.exists(),
        "outdated_restore_pending": request.app.state.outdated_store.restore_pending(),
        "text_counts": status_counts(session),
        "ocr": tesseract_status(config.text.tesseract_cmd, config.text.languages),
        "text_config": config.text,
        "report": None,
        "message": None,
        "error": None,
        **extra,
    }


@router.get("/maintenance", response_class=HTMLResponse)
def maintenance_page(request: Request, session: SessionDep):
    return render(request, "maintenance.html", _context(request, session))


@router.post("/maintenance/check", response_class=HTMLResponse)
async def maintenance_check(request: Request, full: bool = False):
    archive = request.app.state.config.paths.archive

    def run():
        with request.app.state.session_factory() as session:
            return check(session, archive, full=full)

    report = await run_in_threadpool(run)
    return render(request, "_integrity_report.html", {"report": report})


@router.post("/maintenance/backup", response_class=HTMLResponse)
def maintenance_backup(request: Request, session: SessionDep):
    config = request.app.state.config
    path = backup_now(config.database_path, paths.backups_dir(), config.backup.keep, "manual")
    return render(
        request,
        "maintenance.html",
        _context(request, session, message=N_("Backup created.") if path else None),
    )


@router.post("/maintenance/metadata", response_class=HTMLResponse)
def maintenance_metadata(request: Request, session: SessionDep):
    try:
        request.app.state.metadata_exporter.write_now()
        return render(
            request,
            "maintenance.html",
            _context(request, session, message=N_("metadata.json written.")),
        )
    except (OSError, RestorePendingError) as exc:
        return render(request, "maintenance.html", _context(request, session, error=str(exc)))


@router.post("/maintenance/restore", response_class=HTMLResponse)
def maintenance_restore(request: Request, session: SessionDep):
    config = request.app.state.config
    try:
        data = read(config.paths.archive / FILENAME)
        restore(session, data, revision_of(session))
        session.commit()
        request.app.state.outdated_store.restore_metadata()  # Outdated folder, if any
    except (DomainError, OSError, ValueError, KeyError) as exc:
        session.rollback()
        return render(request, "maintenance.html", _context(request, session, error=str(exc)))
    report = check(session, config.paths.archive)
    return render(
        request,
        "maintenance.html",
        _context(
            request,
            session,
            message=N_("Database restored from metadata.json. Integrity check below."),
            report=report,
        ),
    )


@router.post("/maintenance/restore-outdated", response_class=HTMLResponse)
def maintenance_restore_outdated(request: Request, session: SessionDep):
    try:
        count = request.app.state.outdated_store.restore_metadata()
    except (OSError, ValueError, KeyError) as exc:
        return render(request, "maintenance.html", _context(request, session, error=str(exc)))
    message = N_("Outdated store restored.") if count else None
    return render(request, "maintenance.html", _context(request, session, message=message))


@router.post("/maintenance/ocr-check", response_class=HTMLResponse)
def maintenance_ocr_check(request: Request, session: SessionDep):
    available = request.app.state.text_indexer.refresh_ocr()
    message = N_("OCR is available.") if available else None
    return render(request, "maintenance.html", _context(request, session, message=message))
