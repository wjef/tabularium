"""Linking related documents (invoice ↔ payment, corrections, signed versions…)."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tabularium.models import AuditLog, DocumentRelation, RelationType
from tabularium.services import DomainError
from tabularium.services.documents import create_document, update_details
from tabularium.services.relations import (
    add_link,
    choices,
    links_of,
    parse_choice,
    remove_link,
)
from tabularium.web.app import create_app
from tabularium.web.security import CSRF_HEADER

TOKEN = "test-token"


def doc(session, name, title):
    sha = hashlib.sha256(name.encode()).hexdigest()
    d = create_document(
        session,
        sha256=sha,
        stored_path=f"{sha[:2]}/{sha}.pdf",
        original_filename=name,
        size_bytes=1,
        retention_years=5,
    )
    update_details(session, d, title=title)
    return d


@pytest.fixture
def docs(session):
    d = {
        "invoice": doc(session, "a.pdf", "Faktura FV 03/2025"),
        "payment": doc(session, "b.pdf", "Przelew za FV 03/2025"),
        "correction": doc(session, "c.pdf", "Faktura korygująca FK/038"),
        "other": doc(session, "d.pdf", "Umowa najmu"),
    }
    session.commit()
    return d


def phrases(session, document):
    return [(link.phrase, link.other.title) for link in links_of(session, document)]


# --- service ----------------------------------------------------------------------------


def test_one_link_reads_correctly_from_both_sides(session, docs):
    add_link(session, docs["payment"], docs["invoice"], RelationType.PAYMENT_FOR, "out")
    session.commit()
    assert phrases(session, docs["payment"]) == [("is the payment for", "Faktura FV 03/2025")]
    assert phrases(session, docs["invoice"]) == [("is paid by", "Przelew za FV 03/2025")]


def test_incoming_direction_from_the_invoice_page(session, docs):
    # on the invoice page: "this document is corrected by <correction>"
    add_link(session, docs["invoice"], docs["correction"], RelationType.CORRECTION_OF, "in")
    session.commit()
    rel = session.scalars(select(DocumentRelation)).one()
    assert (rel.from_document_id, rel.to_document_id) == (
        docs["correction"].id,
        docs["invoice"].id,
    )
    assert phrases(session, docs["correction"]) == [("is a correction of", "Faktura FV 03/2025")]


def test_guards(session, docs):
    with pytest.raises(DomainError, match="itself"):
        add_link(session, docs["invoice"], docs["invoice"], RelationType.RELATED, "out")
    add_link(session, docs["payment"], docs["invoice"], RelationType.PAYMENT_FOR, "out")
    with pytest.raises(DomainError, match="already linked"):
        add_link(session, docs["invoice"], docs["payment"], RelationType.PAYMENT_FOR, "in")
    add_link(session, docs["invoice"], docs["other"], RelationType.RELATED, "out")
    with pytest.raises(DomainError, match="already linked"):  # "related" works both ways
        add_link(session, docs["other"], docs["invoice"], RelationType.RELATED, "out")
    # a different kind between the same documents is allowed
    add_link(session, docs["payment"], docs["invoice"], RelationType.ATTACHMENT_OF, "out")


def test_links_are_in_both_histories_and_removable(session, docs):
    rel = add_link(session, docs["payment"], docs["invoice"], RelationType.PAYMENT_FOR, "out")
    session.commit()
    remove_link(session, rel.id)
    session.commit()
    for d in (docs["payment"], docs["invoice"]):
        actions = session.scalars(
            select(AuditLog.action)
            .where(AuditLog.object_id == str(d.id), AuditLog.action.like("RELATION_%"))
            .order_by(AuditLog.id)
        ).all()
        assert actions == ["RELATION_ADD", "RELATION_REMOVE"]
    assert links_of(session, docs["invoice"]) == []
    with pytest.raises(DomainError):
        remove_link(session, rel.id)


def test_choices_cover_every_kind_and_parse_back():
    values = [v for v, _ in choices()]
    assert "RELATED:in" not in values  # symmetric: one option is enough
    assert {parse_choice(v)[0] for v in values} == set(RelationType)
    with pytest.raises(DomainError):
        parse_choice("NOPE:out")
    with pytest.raises(DomainError):
        parse_choice("PAYMENT_FOR:sideways")


# --- web --------------------------------------------------------------------------------


@pytest.fixture
def client(config, session_factory):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={CSRF_HEADER: TOKEN})


def test_link_through_the_api(client, docs):
    invoice, payment = str(docs["invoice"].id), str(docs["payment"].id)
    assert client.get(f"/api/v1/documents/{invoice}").json()["links"] == []

    found = client.get("/api/v1/documents", params={"q": "przelew"}).json()["items"]
    assert [d["title"] for d in found] == ["Przelew za FV 03/2025"]

    body = {"other_id": payment, "kind": "PAYMENT_FOR:in"}
    [link] = client.post(f"/api/v1/documents/{invoice}/links", json=body).json()["links"]
    assert (link["phrase"], link["other"]["id"]) == ("is paid by", payment)
    [back] = client.get(f"/api/v1/documents/{payment}").json()["links"]
    assert back["phrase"] == "is the payment for"

    again = client.post(f"/api/v1/documents/{invoice}/links", json=body)
    assert again.status_code == 409 and "already linked" in again.json()["detail"]


def test_remove_link_through_the_api(client, session, docs):
    rel = add_link(session, docs["payment"], docs["invoice"], RelationType.PAYMENT_FOR, "out")
    session.commit()
    r = client.delete(f"/api/v1/documents/{docs['invoice'].id}/links/{rel.id}")
    assert r.status_code == 200 and r.json()["links"] == []


def test_linking_requires_csrf(config, session_factory, docs):
    app = create_app(
        config, csrf_token=TOKEN, session_factory=session_factory, scan_on_startup=False
    )
    plain = TestClient(app, base_url="http://127.0.0.1:8765")
    r = plain.post(
        f"/api/v1/documents/{docs['invoice'].id}/links",
        json={"other_id": str(docs["payment"].id), "kind": "RELATED:out"},
    )
    assert r.status_code == 403
