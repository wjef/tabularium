"""Change history (see docs/requirements.md §6).

``record`` adds the entry to the caller's session, so it is committed in the same
transaction as the change itself — or rolled back with it.
"""

import getpass
import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from tabularium.models import AuditLog


class Action(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    MOVE = "MOVE"
    MERGE = "MERGE"
    DELETE = "DELETE"
    TAG_ADD = "TAG_ADD"
    TAG_REMOVE = "TAG_REMOVE"
    MEMBER_ADD = "MEMBER_ADD"
    MEMBER_REMOVE = "MEMBER_REMOVE"
    OUTDATE = "OUTDATE"
    RESTORE = "RESTORE"


def current_actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — no user name available is not an error
        return "unknown"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def record(
    session: Session,
    action: Action,
    obj: Any,
    *,
    old: Any = None,
    new: Any = None,
    actor: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor or current_actor(),
        action=action.value,
        object_type=type(obj).__tablename__,
        object_id=str(obj.id),
        old_value=_jsonable(old),
        new_value=_jsonable(new),
    )
    session.add(entry)
    return entry
