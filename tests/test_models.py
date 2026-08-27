"""Search-plan, effective-plan, candidate-batch, and report contract checks."""

import pytest
from pydantic import ValidationError

from shared.bounds import MAX_INITIAL_QUERIES, MAX_TOTAL_QUERIES
from shared.models import (
    DateCheck,
    HUMAN_REVIEW_DISCLAIMER,
    RankedEvidence,
    SearchCacheTotals,
    SearchPlanMessage,
    SearchQuery,
)
from tests.conftest import candidate, candidate_batch, effective_plan, report, search_plan


def _followups(count: int, start: int = 1) -> list[SearchQuery]:
    """Build valid follow-up queries for L1 so effective-plan tests vary only their target case."""
    return [
        SearchQuery(id=f"F{start + i}", query_text=f"follow-up {start + i}", limitation_ids=["L1"])
        for i in range(count)
    ]


def test_search_plan_round_trips():
    plan = search_plan()
    restored = SearchPlanMessage.model_validate(plan.model_dump())
    assert restored == plan


def test_search_plan_accepts_twelve_queries():
    queries = [
        SearchQuery(id=f"Q{i}", query_text=f"query {i}", limitation_ids=["L1"])
        for i in range(MAX_INITIAL_QUERIES)
    ]
    assert len(search_plan(queries=queries).queries) == MAX_INITIAL_QUERIES


def test_search_plan_rejects_thirteenth_query():
    queries = [
        SearchQuery(id=f"Q{i}", query_text=f"query {i}", limitation_ids=["L1"])
        for i in range(MAX_INITIAL_QUERIES + 1)
    ]
    with pytest.raises(ValidationError):
        search_plan(queries=queries)


def test_search_plan_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SearchPlanMessage.model_validate({**search_plan().model_dump(), "extra": "nope"})


def test_search_plan_rejects_duplicate_limitation_ids():
    limitation = search_plan().limitations[0]
    with pytest.raises(ValidationError, match="limitation ids must be unique"):
        search_plan(limitations=[limitation, limitation])


def test_search_plan_rejects_duplicate_query_ids():
    query = search_plan().queries[0]
    with pytest.raises(ValidationError, match="query ids must be unique"):
        search_plan(queries=[query, query])


def test_search_plan_rejects_unknown_limitation_ids():
    with pytest.raises(ValidationError, match="unknown limitation ids"):
        search_plan(
            queries=[SearchQuery(id="Q1", query_text="q", limitation_ids=["L9"])]
        )


def test_candidate_requires_published_on_unless_unknown():
    with pytest.raises(ValidationError, match="published_on is required"):
        candidate(published_on=None, date_check=DateCheck.VERIFIED)
    assert candidate(published_on=None, date_check=DateCheck.UNKNOWN).published_on is None


def test_effective_plan_round_trips_and_exposes_job_id():
    """Add three follow-ups; expect all query IDs and the original job ID to remain available."""
    plan = effective_plan(followup_queries=_followups(3))
    assert plan.job_id == "job1"
    assert plan.all_query_ids() == {"Q1", "F1", "F2", "F3"}


def test_effective_plan_rejects_followup_id_colliding_with_original():
    """Reuse original query ID Q1 as a follow-up; expect rejection to preserve unambiguous provenance."""
    followup = SearchQuery(id="Q1", query_text="duplicate id", limitation_ids=["L1"])
    with pytest.raises(ValidationError, match="query ids must be unique"):
        effective_plan(followup_queries=[followup])


def test_effective_plan_rejects_unknown_limitation_links():
    """Link a follow-up to missing limitation L9; expect rejection before Component B can run it."""
    followup = SearchQuery(id="F1", query_text="bad link", limitation_ids=["L9"])
    with pytest.raises(ValidationError, match="unknown limitation ids"):
        effective_plan(followup_queries=[followup])


def test_effective_plan_rejects_exceeding_total_budget():
    """Add more follow-ups than the remaining 40-query budget allows; expect validation to fail."""
    # MAX_TOTAL_QUERIES - MAX_INITIAL_QUERIES is the follow-up cap; one more fails.
    over = MAX_TOTAL_QUERIES - MAX_INITIAL_QUERIES + 1
    with pytest.raises(ValidationError):
        effective_plan(followup_queries=_followups(over))


def test_candidate_batch_rejects_duplicate_urls():
    """Send the same candidate URL twice; expect rejection because each source must be ranked only once."""
    with pytest.raises(ValidationError, match="candidate urls must be unique"):
        candidate_batch(candidates=[candidate(), candidate()])


def test_candidate_batch_rejects_query_ids_missing_from_plan():
    """Claim Q9 found a candidate when Q9 was not executed; expect provenance validation to reject it."""
    with pytest.raises(ValidationError, match="must exist in the effective plan"):
        candidate_batch(candidates=[candidate(query_ids=["Q9"])])


def test_candidate_batch_accepts_followup_query_provenance():
    """Reference executed follow-up F2 on a candidate; expect its valid provenance to be retained."""
    batch = candidate_batch(
        plan=effective_plan(followup_queries=_followups(3)),
        candidates=[candidate(query_ids=["F2"])],
    )
    assert batch.candidates[0].query_ids == ["F2"]


def test_candidate_batch_failure_must_have_no_candidates():
    """Publish a terminal failure with and without candidates; expect only the empty batch to validate."""
    failed = candidate_batch(error_code="search_failed", candidates=[])
    assert failed.error_code == "search_failed"
    with pytest.raises(ValidationError, match="must not include candidates"):
        candidate_batch(error_code="search_failed")


def test_candidate_batch_rejects_unsafe_error_code():
    """Pass provider-like free text as an error code; expect rejection to prevent unsafe message content."""
    with pytest.raises(ValidationError, match="short lowercase token"):
        candidate_batch(error_code="Boom: provider said X!", candidates=[])


def test_cache_totals_accept_total_budget_and_reject_more():
    """Use the full 40-query budget, then exceed it; expect only the bounded totals to validate."""
    totals = SearchCacheTotals(
        searches_run=MAX_TOTAL_QUERIES, cache_hits=0, cache_misses=MAX_TOTAL_QUERIES
    )
    assert totals.searches_run == MAX_TOTAL_QUERIES
    with pytest.raises(ValidationError):
        SearchCacheTotals(searches_run=MAX_TOTAL_QUERIES + 1, cache_hits=0, cache_misses=0)


def test_report_rejects_duplicate_ranks():
    first = RankedEvidence(rank=1, candidate=candidate(), explanation="first")
    second = RankedEvidence(
        rank=1,
        candidate=candidate(url="https://example.com/other"),
        explanation="second",
    )
    with pytest.raises(ValidationError, match="evidence ranks must be unique"):
        report(evidence=[first, second])


def test_report_rejects_duplicate_urls():
    first = RankedEvidence(rank=1, candidate=candidate(), explanation="first")
    second = RankedEvidence(rank=2, candidate=candidate(), explanation="second")
    with pytest.raises(ValidationError, match="evidence urls must be unique"):
        report(evidence=[first, second])


def test_report_rejects_replaced_disclaimer():
    with pytest.raises(ValidationError, match="disclaimer must be the standard"):
        report(disclaimer="A human review is optional.")
    assert report().disclaimer == HUMAN_REVIEW_DISCLAIMER
