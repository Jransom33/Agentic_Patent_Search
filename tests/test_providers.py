"""Deterministic Claude and Exa fakes. No network calls."""

from datetime import date

import pytest
from pydantic import ValidationError

from shared.bounds import MIN_FOLLOWUP_QUERIES
from shared.models import SearchPlanMessage
from shared.providers.claude import FakeClaude, SearchAction, SearchDecision
from shared.providers.exa import FakeExa
from tests.conftest import candidate, search_plan


def test_fake_claude_analysis_builds_valid_search_plan():
    analysis = FakeClaude().analyze_claims("spec text", "claims text", date(2020, 1, 1))
    plan = SearchPlanMessage(
        job_id="job1",
        critical_date=date(2020, 1, 1),
        limitations=analysis.limitations,
        concepts=analysis.concepts,
        queries=analysis.queries,
    )
    assert plan.queries[0].limitation_ids == ["L1"]
    assert "spec text" not in plan.model_dump_json()


def test_fake_claude_ranks_with_and_without_candidates():
    claude = FakeClaude()
    plan = search_plan()
    with_hits = claude.rank_candidates(plan, [candidate()], [])
    assert with_hits.job_id == plan.job_id
    assert len(with_hits.evidence) == 1
    empty = claude.rank_candidates(plan, [], [])
    assert empty.evidence == []
    assert any("No candidates" in note for note in empty.uncertainty_notes)


def test_fake_claude_finishes_immediately_by_default():
    decision = FakeClaude().decide_search(search_plan(), [], [candidate()])
    assert decision.action is SearchAction.FINISH
    assert decision.followup_queries == []


def test_fake_claude_continues_then_finishes():
    claude = FakeClaude(continue_rounds=1)
    plan = search_plan()
    first = claude.decide_search(plan, list(plan.queries), [])
    assert first.action is SearchAction.CONTINUE
    assert len(first.followup_queries) == MIN_FOLLOWUP_QUERIES
    # Follow-up ids are numbered after the tried queries, so no collisions.
    tried = list(plan.queries) + first.followup_queries
    assert len({query.id for query in tried}) == len(tried)
    second = claude.decide_search(plan, tried, [])
    assert second.action is SearchAction.FINISH


def test_search_decision_enforces_followup_rules():
    queries = [
        {"id": f"F{i}", "query_text": f"q{i}", "limitation_ids": ["L1"]} for i in range(2)
    ]
    # continue with fewer than three follow-ups is invalid; finish with any is too.
    with pytest.raises(ValidationError, match="followup queries"):
        SearchDecision(action=SearchAction.CONTINUE, followup_queries=queries)
    with pytest.raises(ValidationError, match="must not include"):
        SearchDecision(action=SearchAction.FINISH, followup_queries=queries[:1])


def test_fake_exa_respects_date_filter_and_clamps_results():
    exa = FakeExa()
    assert exa.search("widget", date(2018, 12, 31), 5) == []
    hits = exa.search("widget", date(2019, 1, 1), 100)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/widget"
    assert exa.search("widget", date(2020, 1, 1), 0) == []


def test_fake_exa_skips_unknown_content_urls():
    exa = FakeExa()
    found = exa.get_contents(
        ["https://example.com/other", "https://example.com/widget"]
    )
    assert [item.url for item in found] == ["https://example.com/widget"]
