"""Worker duplicate skip and publish path. Uses fakes; no paid APIs."""

from search.cache import FakeRedis
from search.worker import handle_plan
from shared.messaging import InMemoryBroker
from shared.models import CandidateBatchMessage
from shared.providers.claude import FakeClaude
from shared.providers.exa import FakeExa
from tests.conftest import search_plan

TOPIC = "candidates"


def test_handle_plan_publishes_batch_and_skips_duplicate():
    """Process the same plan twice; expect one published batch and a Redis done key."""
    cache = FakeRedis()
    broker = InMemoryBroker()
    plan = search_plan()
    kwargs = dict(
        cache=cache,
        exa=FakeExa(),
        decider=FakeClaude(),
        publisher=broker,
        candidates_topic=TOPIC,
    )
    handle_plan(plan, **kwargs)
    assert cache.job_is_done(plan.job_id)
    first = broker.receive(TOPIC, CandidateBatchMessage)
    assert first is not None
    assert first.plan.job_id == plan.job_id
    assert first.error_code is None

    handle_plan(plan, **kwargs)
    assert broker.receive(TOPIC, CandidateBatchMessage) is None
