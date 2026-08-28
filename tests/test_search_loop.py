"""Bounded search loop and cache-aware executor. Uses fakes; no paid APIs."""

from datetime import date

from search.cache import FakeRedis
from search.executor import run_search_pass
from search.loop import DECISION_FAILED, SEARCH_FAILED, run_search_loop
from shared.bounds import MIN_FOLLOWUP_QUERIES
from shared.models import SearchQuery
from shared.providers.claude import FakeClaude, SearchAction, SearchDecision
from shared.providers.exa import FakeExa, SearchHit
from tests.conftest import search_plan


class CountingExa:
    """Wrap FakeExa and count search() calls so miss-then-hit is visible."""

    def __init__(self) -> None:
        self.inner = FakeExa()
        self.calls = 0

    def search(self, query: str, published_before: date, num_results: int) -> list[SearchHit]:
        self.calls += 1
        return self.inner.search(query, published_before, num_results)

    def get_contents(self, urls: list[str]) -> list:
        return self.inner.get_contents(urls)


class AlwaysContinue:
    """Keep proposing three new follow-ups so Python's hard ceilings must stop the loop."""

    def __init__(self) -> None:
        self.calls = 0

    def decide_search(self, plan, tried_queries, candidates) -> SearchDecision:
        self.calls += 1
        start = len(tried_queries)
        return SearchDecision(
            action=SearchAction.CONTINUE,
            coverage_gaps=["still searching"],
            followup_queries=[
                SearchQuery(
                    id=f"F{start + offset}",
                    query_text=f"follow-up {start + offset}",
                    limitation_ids=[plan.limitations[0].id],
                )
                for offset in range(MIN_FOLLOWUP_QUERIES)
            ],
        )


class BadFollowupDecider:
    """Return a well-formed continue whose follow-ups point at a missing limitation."""

    def decide_search(self, plan, tried_queries, candidates) -> SearchDecision:
        return SearchDecision(
            action=SearchAction.CONTINUE,
            coverage_gaps=["gap"],
            followup_queries=[
                SearchQuery(
                    id=f"F{i}",
                    query_text=f"bad {i}",
                    limitation_ids=["L9"],
                )
                for i in range(MIN_FOLLOWUP_QUERIES)
            ],
        )


class FailingExa:
    def search(self, query: str, published_before: date, num_results: int) -> list[SearchHit]:
        raise RuntimeError("exa down")

    def get_contents(self, urls: list[str]) -> list:
        return []


def test_loop_finishes_immediately_when_claude_says_finish():
    """Let FakeClaude finish on the first decision; expect no follow-ups."""
    batch = run_search_loop(
        search_plan(), cache=FakeRedis(), exa=FakeExa(), decider=FakeClaude()
    )
    assert batch.error_code is None
    assert batch.plan.followup_queries == []
    assert batch.candidates[0].query_ids == ["Q1"]


def test_loop_continues_with_new_queries_then_finishes():
    """Continue once; expect the three follow-ups to run and show up in provenance."""
    batch = run_search_loop(
        search_plan(),
        cache=FakeRedis(),
        exa=FakeExa(),
        decider=FakeClaude(continue_rounds=1),
    )
    follow_ids = [query.id for query in batch.plan.followup_queries]
    assert follow_ids == ["F1", "F2", "F3"]
    assert batch.candidates[0].query_ids == ["Q1", "F1", "F2", "F3"]
    assert batch.error_code is None


def test_loop_stops_at_search_pass_ceiling(monkeypatch):
    """Cap passes at 1; expect Claude never to be asked and no follow-ups to run."""
    monkeypatch.setattr("search.loop.MAX_SEARCH_PASSES", 1)
    decider = AlwaysContinue()
    batch = run_search_loop(
        search_plan(), cache=FakeRedis(), exa=FakeExa(), decider=decider
    )
    assert decider.calls == 0
    assert batch.plan.followup_queries == []


def test_loop_stops_at_continuation_decision_ceiling(monkeypatch):
    """Allow one continue; expect that follow-up pass to run, then stop without asking again."""
    monkeypatch.setattr("search.loop.MAX_CONTINUATION_DECISIONS", 1)
    decider = AlwaysContinue()
    batch = run_search_loop(
        search_plan(), cache=FakeRedis(), exa=FakeExa(), decider=decider
    )
    assert decider.calls == 1
    assert [query.id for query in batch.plan.followup_queries] == ["F1", "F2", "F3"]


def test_loop_stops_at_total_query_budget(monkeypatch):
    """Cap total queries at 4; expect one continue of 3 follow-ups and no extra decision."""
    monkeypatch.setattr("search.loop.MAX_TOTAL_QUERIES", 4)
    decider = AlwaysContinue()
    batch = run_search_loop(
        search_plan(), cache=FakeRedis(), exa=FakeExa(), decider=decider
    )
    executed = 1 + len(batch.plan.followup_queries)
    assert executed == 4
    assert decider.calls == 1


def test_loop_rejects_invalid_followups_as_decision_failed():
    """Point follow-ups at unknown limitation L9; expect a terminal failure, no candidates."""
    batch = run_search_loop(
        search_plan(),
        cache=FakeRedis(),
        exa=FakeExa(),
        decider=BadFollowupDecider(),
    )
    assert batch.error_code == DECISION_FAILED
    assert batch.candidates == []


def test_loop_returns_search_failed_when_exa_exhausts_retries():
    """Make every Exa call fail; expect a sanitized search_failed batch."""
    batch = run_search_loop(
        search_plan(), cache=FakeRedis(), exa=FailingExa(), decider=FakeClaude()
    )
    assert batch.error_code == SEARCH_FAILED
    assert batch.candidates == []


def test_search_pass_miss_then_hit_avoids_a_second_exa_call():
    """Run the same query twice on one cache; expect a miss then a hit and one Exa call."""
    cache = FakeRedis()
    exa = CountingExa()
    queries = search_plan().queries
    first = run_search_pass(queries, date(2020, 1, 1), cache=cache, exa=exa)
    second = run_search_pass(queries, date(2020, 1, 1), cache=cache, exa=exa)
    assert first.totals.cache_misses == 1
    assert first.totals.searches_run == 1
    assert second.totals.cache_hits == 1
    assert second.totals.searches_run == 0
    assert exa.calls == 1
