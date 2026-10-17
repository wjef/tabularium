from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from tabularium.retention import can_outdate, default_retain_until

INGESTED = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def test_default_uses_document_year():
    assert default_retain_until(date(2025, 3, 14), INGESTED, 5) == date(2030, 12, 31)


def test_default_falls_back_to_ingest_year():
    assert default_retain_until(None, INGESTED, 5) == date(2031, 12, 31)


def test_zero_years_means_end_of_document_year():
    assert default_retain_until(date(2025, 1, 1), INGESTED, 0) == date(2025, 12, 31)


def test_ingest_year_uses_local_time_not_utc():
    warsaw_winter = timezone(timedelta(hours=1))
    just_after_midnight = datetime(2025, 12, 31, 23, 30, tzinfo=UTC)  # 00:30 on 1 Jan 2026
    assert default_retain_until(None, just_after_midnight, 5, tz=warsaw_winter) == date(
        2031, 12, 31
    )
    assert default_retain_until(None, just_after_midnight, 5, tz=UTC) == date(2030, 12, 31)


def test_negative_years_rejected():
    with pytest.raises(ValueError):
        default_retain_until(None, INGESTED, -1)


def test_can_outdate_only_after_retention_date():
    assert not can_outdate(date(2030, 12, 31), date(2030, 12, 31))
    assert can_outdate(date(2030, 12, 31), date(2031, 1, 1))
