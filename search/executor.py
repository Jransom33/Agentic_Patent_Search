"""One cached, bounded-concurrency Exa search pass.

Checks Redis for each query, runs misses through Exa with MAX_CONCURRENCY
workers, retries transient failures, and caches successful public metadata.
Returns hits tagged with the query id that found them so consolidate() can
merge provenance. Does not log query text.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date

from search.cache import SearchCache
from shared.bounds import MAX_CONCURRENCY, MAX_EXA_RESULTS_PER_QUERY, MAX_RETRIES
from shared.models import SearchCacheTotals, SearchQuery
from shared.providers.exa import ExaClient, SearchHit


@dataclass(frozen=True)
class SearchPassResult:
    """Tagged hits plus cache/Exa counts for this pass only."""

    tagged_hits: list[tuple[str, SearchHit]]
    totals: SearchCacheTotals


def _search_with_retries(
    exa: ExaClient, query_text: str, critical_date: date
) -> list[SearchHit]:
    """Call Exa up to MAX_RETRIES times. Re-raise the last error if all fail.

    An empty hit list is success (nothing found) and will be cached. There is
    no backoff between attempts.
    # ASSUMPTION: MAX_RETRIES is total attempts, not extra retries after the first.
    # INCOMPLETE: no sleep/backoff; enough for a class demo, not production load.
    """
    last_error: Exception | None = None
    for _attempt in range(MAX_RETRIES):
        try:
            return exa.search(query_text, critical_date, MAX_EXA_RESULTS_PER_QUERY)
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def run_search_pass(
    queries: list[SearchQuery],
    critical_date: date,
    *,
    cache: SearchCache,
    exa: ExaClient,
) -> SearchPassResult:
    """Run one batch of queries through cache then Exa.

    Cache lookups stay on this thread so FakeRedis does not need a lock.
    Only Exa calls run in the pool. A query that exhausts retries raises;
    successful misses are cached before that raise so a later retry can hit.
    """
    # query_id -> hits, filled in original query order at the end.
    found: dict[str, list[SearchHit]] = {}
    misses: list[SearchQuery] = []
    cache_hits = 0
    cache_misses = 0
    searches_run = 0

    # Sequential cache check: None is a miss, [] is a cached empty result.
    for query in queries:
        cached = cache.get_hits(query.query_text, critical_date)
        if cached is not None:
            found[query.id] = cached
            cache_hits += 1
        else:
            misses.append(query)
            cache_misses += 1

    # Bounded-concurrency Exa for misses. Cache writes stay on this thread.
    first_error: Exception | None = None
    if misses:
        # Open a pool of up to MAX_CONCURRENCY (4) worker threads.
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            # Submit one Exa job per miss. Map each Future -> its SearchQuery
            # so we know which query finished when as_completed yields it.
            futures = {
                pool.submit(_search_with_retries, exa, query.query_text, critical_date): query
                for query in misses
            }
            # as_completed yields futures in finish order (fastest first), not submit order.
            for future in as_completed(futures):
                # Look up the SearchQuery that belongs to this completed future.
                query = futures[future]
                try:
                    # Blocks until this one job is done; returns hits or raises.
                    hits = future.result()
                except Exception as exc:
                    # Don't abort the loop: other workers may still succeed and
                    # we want those results written to cache before we raise.
                    if first_error is None:
                        first_error = exc
                    continue
                # Success path: store metadata in Redis, keep hits for this pass.
                cache.set_hits(query.query_text, critical_date, hits)
                found[query.id] = hits
                searches_run += 1

    # After every in-flight Exa call finished, fail the pass if any query failed.
    if first_error is not None:
        raise first_error

    # Preserve input query order so consolidation first-seen order is stable.
    tagged: list[tuple[str, SearchHit]] = []
    for query in queries:
        for hit in found.get(query.id, []):
            tagged.append((query.id, hit))

    return SearchPassResult(
        tagged_hits=tagged,
        totals=SearchCacheTotals(
            searches_run=searches_run,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        ),
    )
