"""Translations (gettext, see docs/adr/0008). English strings are the message ids."""

import gettext
from importlib import resources

DOMAIN = "messages"
DEFAULT_LANGUAGE = "en"


def locales_dir() -> str:
    return str(resources.files("tabularium") / "locales")


def get_translations(language: str) -> gettext.NullTranslations:
    """Translations for ``language``; falls back to English (the message ids) if missing."""
    return gettext.translation(DOMAIN, localedir=locales_dir(), languages=[language], fallback=True)


def available_languages() -> list[str]:
    root = resources.files("tabularium") / "locales"
    found = {
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file()
    }
    return sorted(found | {DEFAULT_LANGUAGE})
