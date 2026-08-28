"""FakeRedis query cache and completion keys. No network."""

from datetime import date

from search.cache import FakeRedis
from shared.providers.exa import SearchHit

CRITICAL = date(2020, 1, 1)
HIT = SearchHit(
    title="Example widget publication",
    url="https://example.com/widget",
    published_on=date(2019, 1, 1),
    snippet="A widget used as prior art in tests.",
)


def test_equivalent_query_text_shares_a_cache_entry():
    """Store hits under messy casing/spaces; expect a normalized lookup to hit."""
    cache = FakeRedis()
    cache.set_hits("  Widget   PRIOR art ", CRITICAL, [HIT])
    assert cache.get_hits("widget prior art", CRITICAL) == [HIT]
    assert cache.get_hits("widget prior art", date(2019, 1, 1)) is None


def test_expired_entry_is_a_miss():
    """Advance past the TTL; expect the cached hits to disappear without sleeping 24h."""
    clock = {"t": 0.0}
    cache = FakeRedis(ttl_seconds=10, now=lambda: clock["t"])
    cache.set_hits("widget prior art", CRITICAL, [HIT])
    clock["t"] = 10.0
    assert cache.get_hits("widget prior art", CRITICAL) is None


def test_job_done_flag_tracks_completion():
    """Mark a job done; expect the duplicate-delivery check to see it until TTL."""
    cache = FakeRedis()
    assert cache.job_is_done("job1") is False
    cache.mark_job_done("job1")
    assert cache.job_is_done("job1") is True
    assert cache.job_is_done("job2") is False
