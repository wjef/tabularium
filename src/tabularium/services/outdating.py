"""Outdating documents and restoring them (see docs/adr/0005, docs/requirements.md §7).

Crash-safe order for both directions — a crash at any step loses nothing:
  1. copy the file into the target store and verify its hash
  2. write the target database (commit)
  3. remove the document from the source database (commit)
  4. remove the source file
A retry after a crash between steps finds the copy / row already there and continues.
"""

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tabularium.audit import Action, current_actor, record
from tabularium.models import (
    AuditLog,
    Document,
    DocumentRelation,
    DocumentTag,
    Entity,
    EntityType,
    RelationType,
    Tag,
)
from tabularium.outdated import OutdatedDocument, OutdatedEvent, OutdatedStore
from tabularium.retention import can_outdate
from tabularium.services import DomainError
from tabularium.services.documents import add_tag
from tabularium.services.entities import create_entity, groups_of
from tabularium.services.tags import create_tag, find_by_path, tag_paths
from tabularium.storage import Storage
from tabularium.text import normalize

# columns shared by models.Document and outdated.OutdatedDocument
_DOCUMENT_FIELDS = [
    "id",
    "sha256",
    "stored_path",
    "original_filename",
    "original_relpath",
    "original_mtime",
    "mime_type",
    "size_bytes",
    "title",
    "note",
    "document_date",
    "retain_until",
    "retain_until_manual",
    "ingested_at",
]


def _json(value):
    from tabularium.metadata import _to_json

    return _to_json(value)


def build_snapshot(session: Session, document: Document) -> dict:
    """Everything needed to understand (and restore) the document without the active DB."""
    paths = tag_paths(session)
    tag_ids = session.scalars(
        select(DocumentTag.tag_id).where(DocumentTag.document_id == document.id)
    ).all()
    tags = []
    for tag_id in tag_ids:
        tag = session.get(Tag, tag_id)
        entity = None
        if tag.entity is not None:
            e = tag.entity
            groups = [session.get(Entity, g).name for g in groups_of(session, {e.id})]
            entity = {
                "id": str(e.id),
                "name": e.name,
                "type": e.type.name,
                "aliases": list(e.aliases),
                "attributes": dict(e.attributes),
                "member_of": sorted(groups, key=str.casefold),
            }
        tags.append({"tag_id": str(tag_id), "path": paths[tag_id], "entity": entity})
    tags.sort(key=lambda t: t["path"].casefold())

    relations = []
    for rel in session.scalars(
        select(DocumentRelation).where(
            or_(
                DocumentRelation.from_document_id == document.id,
                DocumentRelation.to_document_id == document.id,
            )
        )
    ):
        outgoing = rel.from_document_id == document.id
        other = session.get(Document, rel.to_document_id if outgoing else rel.from_document_id)
        relations.append(
            {
                "type": rel.type.value,
                "direction": "to" if outgoing else "from",
                "other_id": str(other.id),
                "other_name": other.display_name,
                "note": rel.note,
            }
        )
    history = [
        {
            "at": _json(a.at),
            "actor": a.actor,
            "action": a.action,
            "old": a.old_value,
            "new": a.new_value,
        }
        for a in session.scalars(
            select(AuditLog)
            .where(AuditLog.object_type == "document", AuditLog.object_id == str(document.id))
            .order_by(AuditLog.id)
        )
    ]
    return {"tags": tags, "relations": relations, "history": history}


def _copy_between(source: Storage, target: Storage, key: str, sha256: str) -> None:
    """Copy ``key`` from one store to another and verify it (idempotent)."""
    if target.exists(key) and target.sha256(key) == sha256:
        return
    with source.open(key) as stream:
        staged = target.stage(stream)
    if staged.sha256 != sha256:
        target.discard(staged)
        raise DomainError(f"The archived file {key} does not match its hash; not moved.")
    target.commit(staged, key)


def outdate(
    session: Session,
    archive: Storage,
    store: OutdatedStore,
    document: Document,
    *,
    today: date,
    reason: str | None = None,
) -> None:
    """Move a document out of the active archive. Blocked while ``retain_until`` has not passed."""
    if not can_outdate(document.retain_until, today):
        raise DomainError(
            f"This document must be kept until {document.retain_until.isoformat()}; "
            "it can be outdated from the day after."
        )
    if not archive.exists(document.stored_path):
        raise DomainError(
            "The document's file is missing from the archive; run an integrity check."
        )

    snapshot = build_snapshot(session, document)
    _copy_between(archive, store.storage, document.stored_path, document.sha256)  # 1

    with store.session_factory() as outdated, outdated.begin():  # 2
        if outdated.get(OutdatedDocument, document.id) is None:
            outdated.add(
                OutdatedDocument(
                    **{f: getattr(document, f) for f in _DOCUMENT_FIELDS},
                    outdated_by=current_actor(),
                    reason=(reason or "").strip() or None,
                    snapshot=snapshot,
                )
            )
            outdated.add(
                OutdatedEvent(
                    actor=current_actor(),
                    action="OUTDATED",
                    document_id=str(document.id),
                    data={"reason": reason, "tags": [t["path"] for t in snapshot["tags"]]},
                )
            )

    record(  # 3 — tag links and relations go with the row (ON DELETE CASCADE)
        session,
        Action.OUTDATE,
        document,
        old={"tags": [t["path"] for t in snapshot["tags"]], "relations": snapshot["relations"]},
        new={"reason": reason},
    )
    session.delete(document)
    session.commit()

    archive.remove_moved(document.stored_path)  # 4
    store.write_metadata()


# --- restore --------------------------------------------------------------------------


def _entity_for(session: Session, info: dict) -> Entity:
    entity = session.get(Entity, uuid.UUID(info["id"]))
    if entity is not None:
        return entity
    same_name = session.scalars(
        select(Entity).where(Entity.normalized_name == normalize(info["name"]))
    ).first()
    if same_name is not None:
        return same_name
    entity_type = (
        session.scalars(
            select(EntityType).where(EntityType.normalized_name == normalize(info["type"]))
        ).first()
        or session.scalars(select(EntityType).order_by(EntityType.sort_order)).first()
    )
    if entity_type is None:
        raise DomainError("No entity types exist; create one before restoring.")
    return create_entity(
        session,
        entity_type,
        info["name"],
        aliases=info.get("aliases") or [],
        attributes=info.get("attributes") or {},
    )


def _tag_for(session: Session, info: dict) -> Tag:
    """The original tag if it still exists, else the same path (created where missing)."""
    tag = session.get(Tag, uuid.UUID(info["tag_id"]))
    if tag is not None:
        return tag
    try:
        tag = find_by_path(session, info["path"])
    except DomainError:
        tag = None
    if tag is not None:
        return tag
    parts = info["path"].split(" / ")
    parent = None
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        query = select(Tag).where(Tag.normalized_name == normalize(part))
        query = query.where(
            Tag.parent_id.is_(None) if parent is None else Tag.parent_id == parent.id
        )
        existing = session.scalar(query)
        if existing is not None:
            parent = existing
        elif last and info.get("entity"):
            parent = create_tag(session, parent=parent, entity=_entity_for(session, info["entity"]))
        else:
            parent = create_tag(session, part, parent=parent)
    return parent


def restore_to_active(
    session: Session, archive: Storage, store: OutdatedStore, document_id: uuid.UUID
) -> Document:
    """Bring an outdated document back into the active archive with its tags."""
    with store.session_factory() as outdated:
        row = outdated.get(OutdatedDocument, document_id)
        if row is None:
            raise DomainError("This document is not in the outdated store.")
        fields = {f: getattr(row, f) for f in _DOCUMENT_FIELDS}
        snapshot = row.snapshot

    _copy_between(store.storage, archive, fields["stored_path"], fields["sha256"])  # 1

    document = session.get(Document, document_id)  # 2 (already there after a crash?)
    if document is None:
        if session.scalar(select(Document.id).where(Document.sha256 == fields["sha256"])):
            raise DomainError("The same file is already in the active archive (imported again).")
        document = Document(**fields)
        session.add(document)
        session.flush()
        record(session, Action.RESTORE, document, new={"from": "outdated"})
    for info in snapshot.get("tags", []):
        add_tag(session, document, _tag_for(session, info))
    for rel in snapshot.get("relations", []):
        other = session.get(Document, uuid.UUID(rel["other_id"]))
        if other is None:
            continue  # the related document is itself outdated; the snapshot keeps the link
        pair = (document.id, other.id) if rel["direction"] == "to" else (other.id, document.id)
        exists = session.scalar(
            select(DocumentRelation.id).where(
                DocumentRelation.from_document_id == pair[0],
                DocumentRelation.to_document_id == pair[1],
                DocumentRelation.type == RelationType(rel["type"]),
            )
        )
        if exists is None:
            session.add(
                DocumentRelation(
                    from_document_id=pair[0],
                    to_document_id=pair[1],
                    type=RelationType(rel["type"]),
                    note=rel["note"],
                )
            )
    session.commit()

    with store.session_factory() as outdated, outdated.begin():  # 3
        row = outdated.get(OutdatedDocument, document_id)
        if row is not None:
            outdated.delete(row)
        outdated.add(
            OutdatedEvent(actor=current_actor(), action="RESTORED", document_id=str(document_id))
        )

    store.storage.remove_moved(fields["stored_path"])  # 4
    store.write_metadata()
    return document
