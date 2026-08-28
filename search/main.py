"""Local entry point for Component B: wires fakes and polls for search plans.

Run from the repo root with the virtualenv active:

    python -m search.main

INCOMPLETE: local runs use the in-memory broker, FakeRedis, FakeExa, and
FakeClaude, so nothing is durable and no real APIs are called. Production
wiring (GCP Pub/Sub, Memorystore, the real Claude/Exa adapters) lands with
spec §11.
"""

from search.cache import FakeRedis
from search.worker import run_worker
from shared import logging as shared_logging
from shared.config import load_search_settings
from shared.messaging import InMemoryBroker
from shared.providers.claude import FakeClaude
from shared.providers.exa import FakeExa


def main() -> None:
    """Load Component B settings and run the worker with local fakes."""
    # SearchSettings omits Cloud SQL; Redis/API keys still come from the env
    # even though this local process does not open those connections.
    # ASSUMPTION: FakeClaude() finishes on the first decision (continue_rounds=0).
    # UNCERTAIN: this process's InMemoryBroker starts empty and is not shared
    # with intake.main, so a local end-to-end run needs a later shared broker
    # or a real Pub/Sub topic.
    settings = load_search_settings()
    shared_logging.configure(settings.log_level)
    broker = InMemoryBroker()
    run_worker(
        subscriber=broker,
        publisher=broker,
        plans_topic=settings.pubsub_search_plans_topic,
        candidates_topic=settings.pubsub_candidates_topic,
        cache=FakeRedis(),
        exa=FakeExa(),
        decider=FakeClaude(),
    )


if __name__ == "__main__":
    main()
