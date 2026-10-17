from tabularium.i18n import available_languages, get_translations


def test_english_is_always_available():
    assert "en" in available_languages()


def test_unknown_language_falls_back_to_message_ids():
    assert get_translations("xx").gettext("Archive") == "Archive"
