"""Retention rules (see docs/requirements.md §5).

Default: keep until 31 December of (document year + ``years``). The document year is the
year of ``document_date``, or of the import date when the document date is unknown.
A simplification, not tax advice.
"""

from datetime import date, datetime, tzinfo


def default_retain_until(
    document_date: date | None, ingested_at: datetime, years: int, *, tz: tzinfo | None = None
) -> date:
    """``tz`` defaults to the machine's local time zone."""
    if years < 0:
        raise ValueError("retention years must not be negative")
    # The import year is taken in local time: a file imported at 00:30 on 1 January
    # belongs to the new year, even though it is still 31 December in UTC.
    base_year = document_date.year if document_date else ingested_at.astimezone(tz).year
    return date(base_year + years, 12, 31)


def can_outdate(retain_until: date, today: date) -> bool:
    """A document may be outdated only after its retention date has passed."""
    return today > retain_until
