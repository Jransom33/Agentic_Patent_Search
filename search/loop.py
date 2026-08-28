"""The bounded iterative search loop for one job (spec §9).

Plain Python drives everything: run a search pass, consolidate candidates,
check the hard ceilings, then let Claude choose finish/continue. Claude only
proposes; it cannot exceed MAX_TOTAL_QUERIES, MAX_SEARCH_PASSES, or
MAX_CONTINUATION_DECISIONS. The result is always a CandidateBatchMessage:
either candidates with the effective plan, or a sanitized terminal-failure
code with no candidates.
"""

from pydantic import ValidationError

from search.cache import SearchCache
from search.consolidate import consolidate
from search.executor import run_search_pass
from shared.bounds import (
    MAX_CONTINUATION_DECISIONS,
    MAX_RETRIES,
    MAX_SEARCH_PASSES,
    MAX_TOTAL_QUERIES,
)
from shared.models import (
    Candidate,
    CandidateBatchMessage,
    EffectiveSearchPlan,
    SearchCacheTotals,
    SearchPlanMessage,
    SearchQuery,
)
from shared.providers.claude import SearchAction, SearchDecider, SearchDecision
from shared.providers.exa import ExaClient, SearchHit

# Safe terminal-failure tokens, matching Component A's analysis_failed style.
SEARCH_FAILED = "search_failed"
DECISION_FAILED = "decision_failed"


def _decide_with_retries(
    decider: SearchDecider,
    plan: SearchPlanMessage,
    tried: list[SearchQuery],
    candidates: list[Candidate],
    followups_so_far: list[SearchQuery],
) -> SearchDecision | None:
    """Ask Claude to finish or continue; retry invalid answers a bounded number of times.

    A decision only counts as valid if its follow-up queries also fit the
    effective plan (unique ids, known limitation ids, total budget). Returns
    None when every attempt failed so the caller can emit decision_failed.
    """
    for _attempt in range(MAX_RETRIES):
        try:
            decision = decider.decide_search(plan, tried, candidates)
            # Re-validate follow-ups against the whole plan by building the
            # extended EffectiveSearchPlan; its validators enforce uniqueness,
            # limitation links, and the 40-query budget.
            if decision.action is SearchAction.CONTINUE:
                EffectiveSearchPlan(
                    original=plan,
                    followup_queries=followups_so_far + decision.followup_queries,
                )
            return decision
        except (ValidationError, ValueError):
            # Invalid structured output or follow-ups: try again.
            continue
        except Exception:
            # Provider/network error: also retried, same bounded budget.
            # UNCERTAIN: one retry budget covers both invalid output and
            # provider errors; split them if the demo needs distinct codes.
            continue
    return None


def _batch(
    plan: SearchPlanMessage,
    followups: list[SearchQuery],
    candidates: list[Candidate],
    totals: SearchCacheTotals,
    error_code: str | None = None,
) -> CandidateBatchMessage:
    """Assemble the outbound message; terminal failures carry no candidates."""
    return CandidateBatchMessage(
        plan=EffectiveSearchPlan(original=plan, followup_queries=followups),
        candidates=[] if error_code else candidates,
        totals=totals,
        error_code=error_code,
    )


def run_search_loop(
    plan: SearchPlanMessage,
    *,
    cache: SearchCache,
    exa: ExaClient,
    decider: SearchDecider,
) -> CandidateBatchMessage:
    """Run the whole bounded search for one job and return the batch to publish.

    Sequence per spec §9: execute the pending queries, consolidate, stop at a
    hard limit, otherwise ask Claude. finish publishes; continue queues the
    validated follow-ups for the next pass. Any unrecoverable failure returns
    a terminal-failure batch instead of raising.
    """
    # Loop state. followups only ever contains queries we actually executed
    # (they are queued and run in the same iteration they are accepted).
    followups: list[SearchQuery] = []
    all_hits: list[tuple[str, SearchHit]] = []
    candidates: list[Candidate] = []
    searches_run = cache_hits = cache_misses = 0
    passes = 0
    decisions = 0
    # First pass runs Component A's initial queries (at most 12).
    pending: list[SearchQuery] = list(plan.queries)

    def totals() -> SearchCacheTotals:
        # Counters can never exceed MAX_TOTAL_QUERIES because we never execute
        # more than the total budget; the model re-checks that anyway.
        return SearchCacheTotals(
            searches_run=searches_run,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )

    while pending:
        # --- One Exa pass over the pending queries (cache-aware) ---
        try:
            result = run_search_pass(pending, plan.critical_date, cache=cache, exa=exa)
        except Exception:
            # A query exhausted its retries: fail the job safely rather than
            # publishing partial results as if the search were complete.
            return _batch(plan, followups, [], totals(), SEARCH_FAILED)
        passes += 1
        all_hits.extend(result.tagged_hits)
        searches_run += result.totals.searches_run
        cache_hits += result.totals.cache_hits
        cache_misses += result.totals.cache_misses

        # --- Consolidate everything found so far (dates, dedup, provenance) ---
        candidates = consolidate(all_hits, plan.critical_date)

        # --- Hard ceilings: Python decides these, never Claude ---
        executed = len(plan.queries) + len(followups)
        if (
            executed >= MAX_TOTAL_QUERIES
            or passes >= MAX_SEARCH_PASSES
            or decisions >= MAX_CONTINUATION_DECISIONS
        ):
            break

        # --- Claude chooses finish or continue (bounded retries) ---
        tried = list(plan.queries) + followups
        decision = _decide_with_retries(decider, plan, tried, candidates, followups)
        if decision is None:
            return _batch(plan, followups, [], totals(), DECISION_FAILED)
        decisions += 1
        if decision.action is SearchAction.FINISH:
            break

        # --- Queue follow-ups, trimmed to the remaining total-query budget ---
        remaining = MAX_TOTAL_QUERIES - executed
        accepted = decision.followup_queries[:remaining]
        if not accepted:
            break
        followups.extend(accepted)
        pending = accepted

    # Successful exit: candidates (possibly empty) plus the effective plan.
    return _batch(plan, followups, candidates, totals())
