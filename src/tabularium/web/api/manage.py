"""Management API: tag tree, entities, entity types, memberships, outdated documents,
ingest, export and maintenance."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium import paths
from tabularium.backup import backup_now, list_backups
from tabularium.db import revision_of
from tabularium.i18n import N_
from tabularium.ingest import IngestBusyError
from tabularium.integrity import check
from tabularium.metadata import FILENAME, is_empty, read, restore
from tabularium.models import AuditLog, Entity, EntityMembership, EntityType, Tag
from tabularium.outdated import OutdatedDocument, OutdatedEvent
from tabularium.services import DomainError
from tabularium.services.entities import (
    add_membership,
    create_entity,
    delete_entity,
    groups_of,
    members_of,
    remove_membership,
    update_entity,
)
from tabularium.services.entity_types import create_type, delete_type, rename_type
from tabularium.services.outdating import restore_to_active
from tabularium.services.tags import create_tag, delete_tag, merge_tag, move_tag, rename_tag
from tabularium.textextract import tesseract_status
from tabularium.textindex import status_counts
from tabularium.web.api import schemas as s
from tabularium.web.deps import get_session
from tabularium.web.files import export_zip, file_response

router = APIRouter(tags=["manage"])
SessionDep = Annotated[Session, Depends(get_session)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _one(session: Session, model, object_id: uuid.UUID, what: str):
    obj = session.get(model, object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return obj


# --- tags -------------------------------------------------------------------------------


class TagCreate(_In):
    name: str | None = None
    parent_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None


class TagUpdate(_In):
    name: str | None = None
    parent_id: uuid.UUID | None = None
    move_to_top: bool = False


class TagMerge(_In):
    target_id: uuid.UUID


@router.post("/tags", response_model=s.Message)
def tag_create(session: SessionDep, body: TagCreate):
    parent = _one(session, Tag, body.parent_id, "Parent tag") if body.parent_id else None
    entity = _one(session, Entity, body.entity_id, "Entity") if body.entity_id else None
    tag = create_tag(session, body.name, parent=parent, entity=entity)
    session.commit()
    return s.Message(message=str(tag.id))


@router.patch("/tags/{tag_id}", response_model=s.Message)
def tag_update(session: SessionDep, tag_id: uuid.UUID, body: TagUpdate):
    tag = _one(session, Tag, tag_id, "Tag")
    if body.name is not None:
        rename_tag(session, tag, body.name)
    if body.move_to_top:
        move_tag(session, tag, None)
    elif body.parent_id is not None:
        move_tag(session, tag, _one(session, Tag, body.parent_id, "Parent tag"))
    session.commit()
    return s.Message(message=N_("Tag saved."))


@router.post("/tags/{tag_id}/merge", response_model=s.Message)
def tag_merge(session: SessionDep, tag_id: uuid.UUID, body: TagMerge):
    moved = merge_tag(
        session, _one(session, Tag, tag_id, "Tag"), _one(session, Tag, body.target_id, "Tag")
    )
    session.commit()
    return s.Message(message=str(moved))


@router.delete("/tags/{tag_id}", response_model=s.Message)
def tag_delete(session: SessionDep, tag_id: uuid.UUID):
    delete_tag(session, _one(session, Tag, tag_id, "Tag"))
    session.commit()
    return s.Message(message=N_("Tag deleted."))


# --- entities ---------------------------------------------------------------------------


class EntityCreate(_In):
    name: str
    type_id: uuid.UUID


class EntityUpdate(_In):
    name: str | None = None
    type_id: uuid.UUID | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None


def _ref(e: Entity) -> dict:
    return {"id": e.id, "name": e.name, "type_name": e.type.name}


@router.get("/entities/{entity_id}")
def entity_detail(session: SessionDep, entity_id: uuid.UUID) -> dict:
    e = _one(session, Entity, entity_id, "Entity")
    direct_groups = set(
        session.scalars(select(EntityMembership.group_id).where(EntityMembership.member_id == e.id))
    )
    direct_members = set(
        session.scalars(select(EntityMembership.member_id).where(EntityMembership.group_id == e.id))
    )
    all_groups, all_members = groups_of(session, {e.id}), members_of(session, {e.id})

    def refs(ids):
        return sorted(
            (_ref(session.get(Entity, i)) for i in ids), key=lambda r: r["name"].casefold()
        )

    tag_ids = session.scalars(select(Tag.id).where(Tag.entity_id == e.id)).all()
    return {
        "id": e.id,
        "name": e.name,
        "type_id": e.type_id,
        "type_name": e.type.name,
        "aliases": e.aliases,
        "attributes": e.attributes,
        "tag_ids": tag_ids,
        "groups": refs(direct_groups),
        "indirect_groups": refs(all_groups - direct_groups),
        "members": refs(direct_members),
        "indirect_members": refs(all_members - direct_members),
        "history": [
            s.HistoryEntry.model_validate(a)
            for a in session.scalars(
                select(AuditLog)
                .where(AuditLog.object_type == "entity", AuditLog.object_id == str(e.id))
                .order_by(AuditLog.id.desc())
                .limit(100)
            )
        ],
    }


@router.post("/entities", response_model=s.Message)
def entity_create(session: SessionDep, body: EntityCreate):
    entity_type = _one(session, EntityType, body.type_id, "Entity type")
    entity = create_entity(session, entity_type, body.name)
    session.commit()
    return s.Message(message=str(entity.id))


@router.patch("/entities/{entity_id}", response_model=s.Message)
def entity_update(session: SessionDep, entity_id: uuid.UUID, body: EntityUpdate):
    entity = _one(session, Entity, entity_id, "Entity")
    update_entity(
        session,
        entity,
        name=body.name,
        type_=_one(session, EntityType, body.type_id, "Entity type") if body.type_id else None,
        aliases=body.aliases,
        attributes=body.attributes,
    )
    session.commit()
    return s.Message(message=N_("Entity saved."))


@router.delete("/entities/{entity_id}", response_model=s.Message)
def entity_delete(session: SessionDep, entity_id: uuid.UUID):
    delete_entity(session, _one(session, Entity, entity_id, "Entity"))
    session.commit()
    return s.Message(message=N_("Entity deleted."))


@router.put("/entities/{member_id}/groups/{group_id}", response_model=s.Message)
def membership_add(session: SessionDep, member_id: uuid.UUID, group_id: uuid.UUID):
    add_membership(
        session,
        _one(session, Entity, member_id, "Entity"),
        _one(session, Entity, group_id, "Entity"),
    )
    session.commit()
    return s.Message(message=N_("Added to the group."))


@router.delete("/entities/{member_id}/groups/{group_id}", response_model=s.Message)
def membership_remove(session: SessionDep, member_id: uuid.UUID, group_id: uuid.UUID):
    if not remove_membership(
        session,
        _one(session, Entity, member_id, "Entity"),
        _one(session, Entity, group_id, "Entity"),
    ):
        raise DomainError("Not a member of this group.")
    session.commit()
    return s.Message(message=N_("Removed from the group."))


# --- entity types -----------------------------------------------------------------------


class TypeIn(_In):
    name: str


@router.post("/entity-types", response_model=s.Message)
def type_create(session: SessionDep, body: TypeIn):
    entity_type = create_type(session, body.name)
    session.commit()
    return s.Message(message=str(entity_type.id))


@router.patch("/entity-types/{type_id}", response_model=s.Message)
def type_rename(session: SessionDep, type_id: uuid.UUID, body: TypeIn):
    rename_type(session, _one(session, EntityType, type_id, "Entity type"), body.name)
    session.commit()
    return s.Message(message=N_("Entity type renamed."))


@router.delete("/entity-types/{type_id}", response_model=s.Message)
def type_delete(session: SessionDep, type_id: uuid.UUID):
    delete_type(session, _one(session, EntityType, type_id, "Entity type"))
    session.commit()
    return s.Message(message=N_("Entity type deleted."))


# --- outdated -----------------------------------------------------------------------------


def _outdated_out(row: OutdatedDocument, full: bool = False) -> dict:
    out = {
        "id": row.id,
        "display_name": row.display_name,
        "document_date": row.document_date,
        "outdated_at": row.outdated_at,
        "outdated_by": row.outdated_by,
        "reason": row.reason,
        "tag_paths": row.tag_paths,
        "mime_type": row.mime_type,
    }
    if full:
        out |= {
            "title": row.title,
            "note": row.note,
            "retain_until": row.retain_until,
            "stored_path": row.stored_path,
            "original_filename": row.original_filename,
            "snapshot": row.snapshot,
        }
    return out


@router.get("/outdated")
def outdated_list(request: Request) -> list[dict]:
    with request.app.state.outdated_store.session_factory() as outdated:
        rows = outdated.scalars(
            select(OutdatedDocument).order_by(OutdatedDocument.outdated_at.desc())
        ).all()
        return [_outdated_out(r) for r in rows]


@router.get("/outdated/{document_id}")
def outdated_detail(request: Request, document_id: uuid.UUID) -> dict:
    with request.app.state.outdated_store.session_factory() as outdated:
        row = _one(outdated, OutdatedDocument, document_id, "Outdated document")
        events = outdated.scalars(
            select(OutdatedEvent)
            .where(OutdatedEvent.document_id == str(document_id))
            .order_by(OutdatedEvent.id)
        ).all()
        return _outdated_out(row, full=True) | {
            "events": [
                {"at": e.at, "actor": e.actor, "action": e.action, "data": e.data} for e in events
            ]
        }


@router.get("/outdated/{document_id}/file")
def outdated_file(request: Request, document_id: uuid.UUID, download: bool = False):
    store = request.app.state.outdated_store
    with store.session_factory() as outdated:
        row = _one(outdated, OutdatedDocument, document_id, "Outdated document")
    if not store.storage.exists(row.stored_path):
        raise HTTPException(status_code=404, detail="File is missing from the Outdated folder")
    return file_response(store.storage, row, download=download)


@router.post("/outdated/{document_id}/restore", response_model=s.Message)
def outdated_restore(request: Request, session: SessionDep, document_id: uuid.UUID):
    state = request.app.state
    restore_to_active(session, state.storage, state.outdated_store, document_id)
    return s.Message(message=N_("Document restored to the archive."))


# --- ingest & export --------------------------------------------------------------------


class ExportRequest(_In):
    document_ids: list[uuid.UUID]


@router.post("/ingest/scan")
async def ingest_scan(request: Request) -> dict:
    state = request.app.state
    if state.restore_pending():
        raise DomainError("Restore the database first (Maintenance); ingesting now would block it.")
    try:
        report = await run_in_threadpool(state.ingestor.scan)
    except IngestBusyError as exc:
        raise DomainError(str(exc)) from exc
    state.text_indexer.wake()
    return {
        "imported": report.imported,
        "duplicates": report.duplicates,
        "failed": report.failed,
        "skipped": report.skipped,
        "waiting": report.waiting,
    }


@router.post("/export")
def export(request: Request, session: SessionDep, body: ExportRequest):
    return export_zip(request, session, body.document_ids)


# --- maintenance ------------------------------------------------------------------------


@router.get("/maintenance")
def maintenance_status(request: Request, session: SessionDep) -> dict:
    state = request.app.state
    config = state.config
    exporter = state.metadata_exporter
    ocr = tesseract_status(config.text.tesseract_cmd, config.text.languages)
    return {
        "text_counts": status_counts(session),
        "ocr": {
            "enabled": config.text.ocr,
            "available": ocr.available,
            "command": ocr.command,
            "languages": config.text.languages,
            "message": ocr.message,
        },
        "metadata": {
            "file": str(config.paths.archive / FILENAME),
            "exists": (config.paths.archive / FILENAME).exists(),
            "last_written": exporter.last_written,
            "last_error": exporter.last_error,
        },
        "backups": {
            "dir": str(paths.backups_dir()),
            "keep": config.backup.keep,
            "files": [
                {"name": b.name, "size": b.stat().st_size}
                for b in list_backups(paths.backups_dir())[:20]
            ],
        },
        "can_restore": is_empty(session) and (config.paths.archive / FILENAME).exists(),
        "outdated_restore_pending": state.outdated_store.restore_pending(),
    }


@router.post("/maintenance/check")
async def maintenance_check(request: Request, full: bool = False) -> dict:
    archive = request.app.state.config.paths.archive

    def run():
        with request.app.state.session_factory() as session:
            return check(session, archive, full=full)

    report = await run_in_threadpool(run)
    return {
        "full": report.full,
        "checked": report.checked,
        "ok": report.ok,
        "healthy": report.healthy,
        **{
            name: [vars(p) for p in getattr(report, name)]
            for name in ("missing", "changed", "cloud_only", "orphans")
        },
    }


@router.post("/maintenance/backup", response_model=s.Message)
def maintenance_backup(request: Request):
    config = request.app.state.config
    path = backup_now(config.database_path, paths.backups_dir(), config.backup.keep, "manual")
    if path is None:
        raise DomainError("There is no database to back up yet.")
    return s.Message(message=path.name)


@router.post("/maintenance/metadata", response_model=s.Message)
def maintenance_metadata(request: Request):
    try:
        request.app.state.metadata_exporter.write_now()
    except OSError as exc:
        raise DomainError(str(exc)) from exc
    return s.Message(message=N_("metadata.json written."))


@router.post("/maintenance/restore", response_model=s.Message)
def maintenance_restore(request: Request, session: SessionDep):
    config = request.app.state.config
    try:
        data = read(config.paths.archive / FILENAME)
    except (OSError, ValueError) as exc:
        raise DomainError(str(exc)) from exc
    restore(session, data, revision_of(session))
    session.commit()
    request.app.state.outdated_store.restore_metadata()
    return s.Message(message=N_("Database restored from metadata.json."))


@router.post("/maintenance/restore-outdated", response_model=s.Message)
def maintenance_restore_outdated(request: Request):
    count = request.app.state.outdated_store.restore_metadata()
    return s.Message(message=str(count))


@router.post("/maintenance/ocr-check", response_model=s.Message)
def maintenance_ocr_check(request: Request):
    available = request.app.state.text_indexer.refresh_ocr()
    return s.Message(message="available" if available else "unavailable")
