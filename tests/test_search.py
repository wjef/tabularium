import hashlib
from datetime import date

import pytest
from conftest import etype

from tabularium.services.documents import (
    add_tag,
    create_document,
    set_document_date,
    update_details,
)
from tabularium.services.entities import create_entity
from tabularium.services.search import SearchQuery, search
from tabularium.services.tags import create_tag


def doc(session, name: str, *, title=None, when=None, note=None):
    sha = hashlib.sha256(name.encode()).hexdigest()
    d = create_document(
        session,
        sha256=sha,
        stored_path=f"{sha[:2]}/{sha}.pdf",
        original_filename=name,
        size_bytes=1,
        retention_years=5,
    )
    if title or note:
        update_details(session, d, title=title, note=note)
    if when:
        set_document_date(session, d, when, retention_years=5)
    return d


@pytest.fixture
def world(session):
    """Fictional archive: senders (people/office), categories with sub-tags."""
    jan = create_entity(session, etype(session, "Person"), "Jan Kowalski")
    anna = create_entity(session, etype(session, "Person"), "Anna Nowak")
    office = create_entity(session, etype(session, "Institution"), "Urząd Skarbowy")
    sender, recipient, category = (
        create_tag(session, n) for n in ("sender", "recipient", "category")
    )
    t = {
        "sender": sender,
        "s_jan": create_tag(session, parent=sender, entity=jan),
        "s_office": create_tag(session, parent=sender, entity=office),
        "r_anna": create_tag(session, parent=recipient, entity=anna),
        "r_jan": create_tag(session, parent=recipient, entity=jan),
        "category": category,
    }
    t["taxes"] = create_tag(session, "taxes", parent=category)
    t["pit"] = create_tag(session, "PIT", parent=t["taxes"])
    t["bills"] = create_tag(session, "bills", parent=category)

    d = {
        "pit_from_office": doc(
            session, "pit-2024.pdf", title="Zeznanie PIT 2024", when=date(2025, 4, 1)
        ),
        "bill_from_jan": doc(
            session, "rachunek.pdf", when=date(2024, 1, 15), note="Prąd za grudzień"
        ),
        "letter_to_jan": doc(session, "list.pdf", when=date(2023, 6, 1)),
        "no_date": doc(session, "skan.pdf"),
    }
    add_tag(session, d["pit_from_office"], t["s_office"])
    add_tag(session, d["pit_from_office"], t["pit"])
    add_tag(session, d["bill_from_jan"], t["s_jan"])
    add_tag(session, d["bill_from_jan"], t["bills"])
    add_tag(session, d["letter_to_jan"], t["r_jan"])
    add_tag(session, d["letter_to_jan"], t["s_office"])
    session.commit()
    return {"t": t, "d": d, "e": {"jan": jan, "anna": anna, "office": office}}


def names(session, query: SearchQuery) -> set[str]:
    return {d.original_filename for d in search(session, query)[0]}


def test_empty_query_returns_everything_newest_first(session, world):
    docs, total = search(session, SearchQuery())
    assert total == 4
    assert [d.original_filename for d in docs] == [
        "pit-2024.pdf",
        "rachunek.pdf",
        "list.pdf",
        "skan.pdf",  # no document date: last
    ]


def test_selecting_a_facet_includes_all_its_tags(session, world):
    t = world["t"]
    assert names(session, SearchQuery(tag_ids=[t["sender"].id])) == {
        "pit-2024.pdf",
        "rachunek.pdf",
        "list.pdf",
    }


def test_parent_tag_includes_children(session, world):
    t = world["t"]
    assert names(session, SearchQuery(tag_ids=[t["taxes"].id])) == {"pit-2024.pdf"}
    assert names(session, SearchQuery(tag_ids=[t["category"].id])) == {
        "pit-2024.pdf",
        "rachunek.pdf",
    }


def test_or_within_facet_and_across_facets(session, world):
    t = world["t"]
    # sender = Jan OR office
    assert names(session, SearchQuery(tag_ids=[t["s_jan"].id, t["s_office"].id])) == {
        "pit-2024.pdf",
        "rachunek.pdf",
        "list.pdf",
    }
    # (sender = office) AND (category = taxes)
    assert names(session, SearchQuery(tag_ids=[t["s_office"].id, t["taxes"].id])) == {
        "pit-2024.pdf"
    }


def test_entity_filter_finds_anything_involving_them(session, world):
    e = world["e"]
    assert names(session, SearchQuery(entity_ids=[e["jan"].id])) == {"rachunek.pdf", "list.pdf"}
    assert names(session, SearchQuery(entity_ids=[e["jan"].id, e["anna"].id])) == {
        "rachunek.pdf",
        "list.pdf",
    }
    assert names(session, SearchQuery(entity_ids=[e["anna"].id])) == set()


def test_text_ignores_case_and_diacritics_and_searches_title_name_note(session, world):
    assert names(session, SearchQuery(text="zeznanie")) == {"pit-2024.pdf"}
    assert names(session, SearchQuery(text="PRAD grudzien")) == {"rachunek.pdf"}  # note
    assert names(session, SearchQuery(text="skan")) == {"skan.pdf"}  # file name
    assert names(session, SearchQuery(text="100%")) == set()  # wildcards are literal


def test_date_range_on_document_date(session, world):
    q = SearchQuery(date_from=date(2024, 1, 1), date_to=date(2024, 12, 31))
    assert names(session, q) == {"rachunek.pdf"}
    assert names(session, SearchQuery(date_from=date(2024, 1, 1))) == {
        "pit-2024.pdf",
        "rachunek.pdf",
    }


def test_limit_reports_total(session, world):
    docs, total = search(session, SearchQuery(), limit=2)
    assert len(docs) == 2 and total == 4


def test_ticking_a_tag_and_one_below_it_narrows_to_the_lower_one(session, world):
    """The founding example: choose "sender", then a person → documents from that person."""
    t = world["t"]
    assert names(session, SearchQuery(tag_ids=[t["sender"].id, t["s_jan"].id])) == {"rachunek.pdf"}
    # two refinements in the same facet still combine with OR
    assert names(
        session, SearchQuery(tag_ids=[t["sender"].id, t["s_jan"].id, t["s_office"].id])
    ) == {"pit-2024.pdf", "rachunek.pdf", "list.pdf"}
