"""Search-plan, candidate-batch, and report contract checks."""

import pytest
from pydantic import ValidationError

from shared.bounds import MAX_INITIAL_QUERIES
from shared.models import (
    DateCheck,
    HUMAN_REVIEW_DISCLAIMER,
    RankedEvidence,
    SearchPlanMessage,
    SearchQuery,
)
from tests.conftest import candidate, candidate_batch, report, search_plan


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


def test_candidate_batch_rejects_duplicate_urls():
    with pytest.raises(ValidationError, match="candidate urls must be unique"):
        candidate_batch(candidates=[candidate(), candidate()])


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
