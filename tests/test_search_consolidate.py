"""Date filtering, URL dedup, and query provenance. No I/O."""

from datetime import date

from search.consolidate import classify_date, consolidate, normalize_url
from shared.models import DateCheck
from shared.providers.exa import SearchHit


def _hit(url: str, published_on: date | None = date(2019, 1, 1), **overrides) -> SearchHit:
    data = dict(
        title="A paper",
        url=url,
        published_on=published_on,
        snippet="A widget is disclosed.",
    )
    data.update(overrides)
    return SearchHit(**data)


def test_normalize_url_drops_tracking_and_unifies_host_path():
    """Feed a tracked, mixed-case URL; expect the same key as the clean form."""
    dirty = "HTTPS://Example.com/paper/?utm_source=x&gclid=abc#intro"
    assert normalize_url(dirty) == "https://example.com/paper"


def test_classify_date_same_day_is_verified():
    """Use the critical date itself; expect verified, not after-critical-date."""
    day = date(2020, 1, 1)
    assert classify_date(day, day) is DateCheck.VERIFIED
    assert classify_date(None, day) is DateCheck.UNKNOWN
    assert classify_date(date(2020, 1, 2), day) is DateCheck.AFTER_CRITICAL_DATE


def test_consolidate_drops_after_critical_date_and_keeps_unknown():
    """Mix dated, undated, and too-late hits; expect only prior-art and unknown rows."""
    critical = date(2020, 1, 1)
    rows = consolidate(
        [
            ("Q1", _hit("https://example.com/old", date(2019, 1, 1))),
            ("Q1", _hit("https://example.com/late", date(2021, 1, 1))),
            ("Q1", _hit("https://example.com/undated", None)),
        ],
        critical,
    )
    urls = [item.url for item in rows]
    assert urls == ["https://example.com/old", "https://example.com/undated"]
    assert rows[0].date_check is DateCheck.VERIFIED
    assert rows[1].date_check is DateCheck.UNKNOWN


def test_consolidate_merges_duplicate_urls_and_unions_query_ids():
    """Find the same paper via two queries; expect one candidate with both ids."""
    rows = consolidate(
        [
            ("Q1", _hit("https://Example.com/paper?utm_source=x")),
            ("Q2", _hit("https://example.com/paper/")),
        ],
        date(2020, 1, 1),
    )
    assert len(rows) == 1
    assert rows[0].url == "https://example.com/paper"
    assert rows[0].query_ids == ["Q1", "Q2"]
