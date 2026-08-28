"""Component C ranking pipeline. Uses fakes; no paid APIs."""

import pytest

from report.pipeline import FETCH_FAILED_NOTE, RANKING_FAILED, RankingFailedError, run_ranking
from shared.bounds import MAX_RETRIES
from shared.models import Citation, RankedEvidence, Report
from shared.providers.claude import FakeClaude
from shared.providers.exa import FakeExa, SearchHit
from tests.conftest import candidate, candidate_batch, search_plan


class FailingExa:
    """Raise on every get_contents call so per-URL retries can exhaust."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, published_before, num_results: int) -> list[SearchHit]:
        return []

    def get_contents(self, urls: list[str]) -> list:
        self.calls += 1
        raise RuntimeError("exa down")


class FailingRanker:
    """Always raise so ranking retries can exhaust."""

    def __init__(self) -> None:
        self.calls = 0

    def rank_candidates(self, plan, candidates, contents) -> Report:
        self.calls += 1
        raise RuntimeError("claude down")


class InventedUrlRanker:
    """Rank a URL that was never in the batch."""

    def rank_candidates(self, plan, candidates, contents) -> Report:
        return Report(
            job_id=plan.job_id,
            critical_date=plan.critical_date,
            evidence=[
                RankedEvidence(
                    rank=1,
                    candidate=candidate(url="https://example.com/invented"),
                    explanation="Invented source for tests.",
                )
            ],
        )


class MismatchedCitationRanker:
    """Cite a URL that does not match the ranked candidate."""

    def rank_candidates(self, plan, candidates, contents) -> Report:
        return Report(
            job_id=plan.job_id,
            critical_date=plan.critical_date,
            evidence=[
                RankedEvidence(
                    rank=1,
                    candidate=candidates[0],
                    explanation="Citation points at a different URL.",
                    citations=[
                        Citation(url="https://example.com/other", passage="quoted text")
                    ],
                )
            ],
        )


def test_run_ranking_happy_path_fetches_and_ranks():
    """FakeExa returns text for the canned URL; expect one ranked candidate."""
    result = run_ranking(candidate_batch(), ranker=FakeClaude(), exa=FakeExa())
    assert result.job_id == search_plan().job_id
    assert len(result.evidence) == 1
    assert result.evidence[0].candidate.url == "https://example.com/widget"
    assert FETCH_FAILED_NOTE not in result.uncertainty_notes


def test_run_ranking_degrades_when_content_fetch_fails():
    """Exhaust Exa retries; expect ranking to continue with the fetch-failure note."""
    exa = FailingExa()
    result = run_ranking(candidate_batch(), ranker=FakeClaude(), exa=exa)
    assert exa.calls == MAX_RETRIES
    assert FETCH_FAILED_NOTE in result.uncertainty_notes
    assert len(result.evidence) == 1


def test_run_ranking_raises_after_ranking_retries_exhausted():
    """Make every ranking call fail; expect RankingFailedError after MAX_RETRIES."""
    ranker = FailingRanker()
    with pytest.raises(RankingFailedError) as exc:
        run_ranking(candidate_batch(), ranker=ranker, exa=FakeExa())
    assert exc.value.error_code == RANKING_FAILED
    assert ranker.calls == MAX_RETRIES


def test_run_ranking_rejects_invented_urls():
    """Rank a URL not in the batch; expect the same terminal ranking failure."""
    with pytest.raises(RankingFailedError):
        run_ranking(candidate_batch(), ranker=InventedUrlRanker(), exa=FakeExa())


def test_run_ranking_rejects_mismatched_citations():
    """Cite a different URL than the ranked candidate; expect ranking_failed."""
    with pytest.raises(RankingFailedError):
        run_ranking(candidate_batch(), ranker=MismatchedCitationRanker(), exa=FakeExa())
