"""Pure candidate consolidation: dates, URLs, duplicates, and query provenance.

No I/O. The executor will pass (query_id, SearchHit) pairs; this module returns
Candidate rows ready for CandidateBatchMessage. Component C still decides
whether a document actually discloses a limitation.
"""

from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from shared.bounds import MAX_CANDIDATES, MAX_SNIPPET_LENGTH, MAX_TOTAL_QUERIES
from shared.models import Candidate, DateCheck
from shared.providers.exa import SearchHit

# Candidate.title has a Field cap of 300 that is not in bounds.py.
_MAX_TITLE_LENGTH = 300
_MAX_URL_LENGTH = 2048

# Ad/analytics params that don't change which page the URL points to.
# INCOMPLETE: common trackers only. Other click-ids will stay in the URL.
_TRACKING_PARAMS = frozenset({"gclid", "gbraid", "wbraid", "fbclid", "mc_cid", "mc_eid"})


@dataclass
class _Bucket:
    """First-seen metadata for one normalized URL, plus every query that found it."""

    title: str
    url: str
    published_on: date | None
    snippet: str
    date_check: DateCheck
    query_ids: list[str] = field(default_factory=list)


def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment and tracking params, strip default ports.

    Returns '' when there is no host so the caller can skip the hit. Path case
    is left alone because some servers treat it as significant.
    """
    # Split into scheme, host, path, query, fragment, etc.
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    # Drop :80 / :443; keep any other port so two services on one host stay distinct.
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    # Keep real query params (e.g. id=42); drop utm_* and known click-ids.
    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if name.lower() not in _TRACKING_PARAMS and not name.lower().startswith("utm_")
        ]
    )
    # /paper/ and /paper should merge as the same document.
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # Empty strings: unused "params" field, and no fragment (#section).
    return urlunparse((scheme, netloc, path, "", query, ""))


def classify_date(published_on: date | None, critical_date: date) -> DateCheck:
    """Re-check Exa's date against the job critical date after retrieval.

    Same-day counts as prior art (verified). Missing dates stay unknown so
    Component C can still look at them. After-critical-date is a separate
    state; consolidate() drops those rows.
    """
    # No date from Exa: keep the hit, mark it unknown.
    if published_on is None:
        return DateCheck.UNKNOWN
    # Published too late to be prior art for this job.
    if published_on > critical_date:
        return DateCheck.AFTER_CRITICAL_DATE
    # On or before the critical date.
    return DateCheck.VERIFIED


def _fallback_title(hit: SearchHit, url: str) -> str:
    # Candidate requires a non-empty title; fall back to the URL if Exa omitted one.
    return ((hit.title or "").strip() or url)[:_MAX_TITLE_LENGTH]


def _fallback_snippet(hit: SearchHit, title: str) -> str:
    # Same idea for snippets: reuse the title if Exa gave nothing.
    return ((hit.snippet or "").strip() or title)[:MAX_SNIPPET_LENGTH]


def consolidate(hits: list[tuple[str, SearchHit]], critical_date: date) -> list[Candidate]:
    """Turn tagged Exa hits into a bounded, de-duplicated candidate list.

    hits is (query_id, SearchHit) in retrieval order. Duplicate URLs merge
    into one Candidate whose query_ids lists every query that found it.
    Hits after the critical date are dropped. Caps at MAX_CANDIDATES.
    """
    # buckets: normalized URL -> merged metadata for that document
    buckets: dict[str, _Bucket] = {}
    # order: first-seen URLs, so we can later take the first MAX_CANDIDATES
    order: list[str] = []

    # Walk every Exa hit, tagged with the query that found it.
    for query_id, hit in hits:
        # Skip blank query ids; Candidate requires at least one real id.
        if not query_id:
            continue

        # Clean the URL so tracking params / casing don't create false duplicates.
        url = normalize_url(hit.url)
        # Skip unparseable URLs or ones longer than the Candidate model allows.
        if not url or len(url) > _MAX_URL_LENGTH:
            continue

        # Decide verified / unknown / after_critical_date for this hit.
        check = classify_date(hit.published_on, critical_date)
        # ASSUMPTION: "enforce the critical date" means drop these, not publish
        # AFTER_CRITICAL_DATE rows. Unknown dates are kept.
        if check is DateCheck.AFTER_CRITICAL_DATE:
            continue

        # Have we already created a bucket for this normalized URL?
        bucket = buckets.get(url)
        if bucket is None:
            # New document: fill title/snippet (with fallbacks) and start provenance.
            title = _fallback_title(hit, url)
            buckets[url] = _Bucket(
                title=title,
                url=url,
                published_on=hit.published_on,
                snippet=_fallback_snippet(hit, title),
                date_check=check,
                query_ids=[query_id],
            )
            # Remember first-seen order for the final size cap.
            order.append(url)
            continue

        # Duplicate URL: add this query to provenance if it isn't already listed.
        if query_id not in bucket.query_ids:
            bucket.query_ids.append(query_id)
        # UNCERTAIN: if two hits disagree on the date, keep the first dated one.
        if bucket.date_check is DateCheck.UNKNOWN and check is DateCheck.VERIFIED:
            bucket.published_on = hit.published_on
            bucket.date_check = check

    # Convert buckets into validated Candidate models for the outbound message.
    candidates: list[Candidate] = []
    # Only the first MAX_CANDIDATES (25) distinct URLs, in first-seen order.
    for url in order[:MAX_CANDIDATES]:
        bucket = buckets[url]
        # Defensive: a Candidate must have at least one query_id.
        if not bucket.query_ids:
            continue
        candidates.append(
            Candidate(
                title=bucket.title,
                url=bucket.url,
                published_on=bucket.published_on,
                snippet=bucket.snippet,
                date_check=bucket.date_check,
                # Cap provenance length to the total-query budget.
                query_ids=bucket.query_ids[:MAX_TOTAL_QUERIES],
            )
        )
    return candidates
