"""Component C worker: duplicates, failures, and report storage. Uses fakes."""

from report.worker import handle_batch
from shared.bounds import JobStatus
from shared.db import InMemoryJobStore
from shared.providers.claude import FakeClaude
from shared.providers.exa import FakeExa
from tests.conftest import candidate_batch, effective_plan, search_plan


class CountingRanker:
    """Wrap FakeClaude and count rank_candidates calls."""

    def __init__(self) -> None:
        self.inner = FakeClaude()
        self.calls = 0

    def rank_candidates(self, plan, candidates, contents):
        self.calls += 1
        return self.inner.rank_candidates(plan, candidates, contents)


def _store_and_batch(**overrides):
    """Create a job whose id matches the batch so set_status can succeed."""
    store = InMemoryJobStore()
    job_id = store.create_job()
    batch = candidate_batch(
        plan=effective_plan(original=search_plan(job_id=job_id)),
        **overrides,
    )
    return store, batch


def test_handle_batch_stores_report_and_skips_duplicate():
    """Process the same batch twice; expect one report and no second ranking call."""
    store, batch = _store_and_batch()
    ranker = CountingRanker()
    kwargs = dict(store=store, ranker=ranker, exa=FakeExa())
    handle_batch(batch, **kwargs)
    job = store.get_job(batch.plan.job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    first = store.get_report(batch.plan.job_id)
    assert first is not None
    assert ranker.calls == 1

    handle_batch(batch, **kwargs)
    assert store.get_report(batch.plan.job_id) is first
    assert ranker.calls == 1


def test_handle_batch_marks_terminal_failure_without_ranking():
    """A search_failed batch should fail the job and never call the ranker."""
    store, batch = _store_and_batch(candidates=[], error_code="search_failed")
    ranker = CountingRanker()
    handle_batch(batch, store=store, ranker=ranker, exa=FakeExa())
    job = store.get_job(batch.plan.job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error_code == "search_failed"
    assert store.get_report(batch.plan.job_id) is None
    assert ranker.calls == 0


def test_handle_batch_completes_empty_candidate_list():
    """A successful search with no hits still stores a completed report."""
    store, batch = _store_and_batch(candidates=[])
    handle_batch(batch, store=store, ranker=FakeClaude(), exa=FakeExa())
    job = store.get_job(batch.plan.job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    stored = store.get_report(batch.plan.job_id)
    assert stored is not None
    assert stored.evidence == []
