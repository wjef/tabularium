"""Search & browse: filters on the left, results, preview of the selected document.

The filter is a plain GET form, so every search is a bookmarkable URL.
"""

from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tabularium.models import Document, Entity
from tabularium.services.entity_types import list_types
from tabularium.services.search import SearchQuery, search
from tabularium.web.deps import get_session, parse_uuid, render
from tabularium.web.routes_documents import DOCUMENT, document_tag_paths, page_context
from tabularium.web.routes_tags import build_tree

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
RESULT_LIMIT = 500


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    session: SessionDep,
    tag: Annotated[list[str] | None, Query()] = None,
    entity: Annotated[list[str] | None, Query()] = None,
    q: str = "",
    date_from: Annotated[str, Query(alias="from")] = "",
    date_to: Annotated[str, Query(alias="to")] = "",
    id: str = "",
):
    query = SearchQuery(
        tag_ids=[t for t in (parse_uuid(v) for v in tag or []) if t],
        entity_ids=[e for e in (parse_uuid(v) for v in entity or []) if e],
        text=q,
        date_from=_date(date_from),
        date_to=_date(date_to),
    )
    documents, total = search(session, query, limit=RESULT_LIMIT)

    params = [("tag", str(t)) for t in query.tag_ids] + [
        ("entity", str(e)) for e in query.entity_ids
    ]
    params += [(k, v) for k, v in (("q", q), ("from", date_from), ("to", date_to)) if v]
    base = urlencode(params)

    selected = session.get(Document, parse_uuid(id)) if parse_uuid(id) else None
    roots, _ = build_tree(session)
    entities = session.scalars(select(Entity).order_by(Entity.normalized_name)).all()
    by_type = {t.id: (t, []) for t in list_types(session)}
    for e in entities:
        by_type[e.type_id][1].append(e)

    context = page_context(request, session, selected, DOCUMENT)
    context.update(
        {
            "roots": roots,
            "entity_groups": [g for g in by_type.values() if g[1]],
            "query": query,
            "q": q,
            "date_from": date_from,
            "date_to": date_to,
            "selected_tags": set(query.tag_ids),
            "selected_entities": set(query.entity_ids),
            "results": documents,
            "total": total,
            "limit": RESULT_LIMIT,
            "result_tags": document_tag_paths(session, [d.id for d in documents]),
            "base_query": base,
        }
    )
    return render(request, "search.html", context)
