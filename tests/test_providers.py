"""Deterministic Claude and Exa fakes. No network calls."""

from datetime import date

from shared.models import SearchPlanMessage
from shared.providers.claude import FakeClaude
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
