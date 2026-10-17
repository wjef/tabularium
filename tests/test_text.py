import pytest

from tabularium.text import clean_name, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Urząd Skarbowy", "urzad skarbowy"),
        ("ŁÓDŹ", "lodz"),
        ("Paweł Zając", "pawel zajac"),
        ("  Jan   Kowalski ", "jan kowalski"),
        ("Straße", "strasse"),
        ("ＡＢＣ", "abc"),  # full-width letters
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_clean_name_collapses_whitespace_and_rejects_empty():
    assert clean_name("  Anna \t Nowak ") == "Anna Nowak"
    with pytest.raises(ValueError):
        clean_name("   ")
