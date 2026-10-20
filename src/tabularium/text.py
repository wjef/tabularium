"""Text normalization for uniqueness checks and search (see docs/requirements.md §4.3)."""

import unicodedata

# Letters that Unicode decomposition does not reduce to a base letter.
_EXTRA = str.maketrans({"ł": "l", "Ł": "l", "ß": "ss", "đ": "d", "Đ": "d", "ø": "o", "Ø": "o"})


def normalize(text: str) -> str:
    """Case-fold, strip diacritics (``Urząd`` → ``urzad``) and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_EXTRA))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


class EmptyNameError(ValueError):
    """A name (tag, entity, type, alias) was empty; the message is meant for the user."""


def clean_name(text: str) -> str:
    """User-facing name: trimmed, inner whitespace collapsed. Raises on empty."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise EmptyNameError("A name is required.")
    return cleaned
