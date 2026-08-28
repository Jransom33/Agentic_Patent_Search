"""Redis cache for equivalent Exa queries and job-completion markers.

Component B checks this before each Exa call and records a completion key
after publishing so duplicate Pub/Sub deliveries can be acknowledged. Only
successful public hit metadata is stored, never full documents. FakeRedis
is the in-memory stand-in for tests and local runs; RedisSearchCache is the
thin redis-py wrapper for Memorystore.
"""

import json
import time
from datetime import date
from typing import Callable, Protocol

from shared.bounds import REDIS_CACHE_TTL_SECONDS
from shared.providers.exa import SearchHit

# ASSUMPTION: default Memorystore port, no AUTH/TLS. SearchSettings only
# exposes REDIS_HOST, so a passworded instance would need a later config field.
_REDIS_PORT = 6379


def _query_key(query_text: str, critical_date: date) -> str:
    """Redis key for one equivalent query: lowercase, collapsed whitespace, date.

    ASSUMPTION: putting the normalized query in the key is acceptable; these
    are search strings, not uploaded spec/claims text. Hashing would hide them.
    """
    normalized = " ".join(query_text.lower().split())
    return f"search:q:{critical_date.isoformat()}:{normalized}"


def _done_key(job_id: str) -> str:
    return f"search:done:{job_id}"


def _encode_hits(hits: list[SearchHit]) -> str:
    """Serialize hit metadata to JSON. Dates become ISO strings; None stays null."""
    return json.dumps(
        [
            {
                "title": hit.title,
                "url": hit.url,
                "published_on": hit.published_on.isoformat() if hit.published_on else None,
                "snippet": hit.snippet,
            }
            for hit in hits
        ]
    )


def _decode_hits(raw: str) -> list[SearchHit] | None:
    """Parse cached JSON back into SearchHit rows.

    Returns None on corrupt data so the caller treats it as a miss and can
    re-query Exa instead of crashing the search loop.
    """
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            return None
        hits: list[SearchHit] = []
        for item in items:
            published = item.get("published_on")
            hits.append(
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    published_on=date.fromisoformat(published) if published else None,
                    snippet=item["snippet"],
                )
            )
        return hits
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


class SearchCache(Protocol):
    """Cache of Exa hit metadata plus best-effort job-completion flags."""

    def get_hits(self, query_text: str, critical_date: date) -> list[SearchHit] | None:
        """Return cached hits for an equivalent query, or None on a miss."""
        ...

    def set_hits(self, query_text: str, critical_date: date, hits: list[SearchHit]) -> None:
        """Store successful public result metadata for REDIS_CACHE_TTL_SECONDS."""
        ...

    def job_is_done(self, job_id: str) -> bool:
        """True when this job already published a candidate batch."""
        ...

    def mark_job_done(self, job_id: str) -> None:
        """Record that the job published. Best-effort; the worker decides errors."""
        ...


class FakeRedis:
    """In-memory SearchCache with TTL. Never opens a network connection."""

    def __init__(
        self,
        ttl_seconds: int = REDIS_CACHE_TTL_SECONDS,
        now: Callable[[], float] | None = None,
    ) -> None:
        # now is injectable so later tests can expire keys without sleeping 24h.
        self._ttl = ttl_seconds
        self._now = now or time.monotonic
        self._store: dict[str, tuple[str, float]] = {}

    def _read(self, key: str) -> str | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if self._now() >= expires_at:
            del self._store[key]
            return None
        return value

    def _write(self, key: str, value: str) -> None:
        self._store[key] = (value, self._now() + self._ttl)

    def get_hits(self, query_text: str, critical_date: date) -> list[SearchHit] | None:
        """Look up cached hits; None means miss or an expired/corrupt entry."""
        key = _query_key(query_text, critical_date)
        raw = self._read(key)
        if raw is None:
            return None
        hits = _decode_hits(raw)
        # Drop corrupt rows so the next set_hits can replace them.
        if hits is None:
            self._store.pop(key, None)
        return hits

    def set_hits(self, query_text: str, critical_date: date, hits: list[SearchHit]) -> None:
        """Cache this query's hits, including an empty list from a successful search."""
        self._write(_query_key(query_text, critical_date), _encode_hits(hits))

    def job_is_done(self, job_id: str) -> bool:
        """True if mark_job_done ran for this id and the TTL has not expired."""
        return self._read(_done_key(job_id)) is not None

    def mark_job_done(self, job_id: str) -> None:
        """Set the completion flag. Value is a token; only presence matters."""
        self._write(_done_key(job_id), "1")


class RedisSearchCache:
    """SearchCache backed by redis-py. Tests should keep using FakeRedis."""

    def __init__(self, host: str, ttl_seconds: int = REDIS_CACHE_TTL_SECONDS) -> None:
        # Import here so FakeRedis tests do not need a live Redis client loaded.
        # INCOMPLETE: no socket timeout or AUTH; Memorystore in the class demo
        # is expected to be reachable on the default port with no password.
        import redis

        self._client = redis.Redis(host=host, port=_REDIS_PORT, decode_responses=True)
        self._ttl = ttl_seconds

    def get_hits(self, query_text: str, critical_date: date) -> list[SearchHit] | None:
        """GET the equivalent-query key. Corrupt JSON is a miss, not a crash."""
        key = _query_key(query_text, critical_date)
        raw = self._client.get(key)
        if raw is None:
            return None
        hits = _decode_hits(raw)
        if hits is None:
            self._client.delete(key)
        return hits

    def set_hits(self, query_text: str, critical_date: date, hits: list[SearchHit]) -> None:
        """SET the hit list with the shared 24h TTL. Does not log query text."""
        self._client.set(_query_key(query_text, critical_date), _encode_hits(hits), ex=self._ttl)

    def job_is_done(self, job_id: str) -> bool:
        """True when the completion key exists. Redis errors propagate to the worker."""
        return self._client.get(_done_key(job_id)) is not None

    def mark_job_done(self, job_id: str) -> None:
        """SET the completion key. The worker treats this as best-effort."""
        # UNCERTAIN: completion keys share REDIS_CACHE_TTL_SECONDS; a duplicate
        # delivery after 24h would search again. Fine for the class demo.
        self._client.set(_done_key(job_id), "1", ex=self._ttl)
