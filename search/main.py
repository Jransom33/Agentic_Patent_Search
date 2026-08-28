"""Entry point for Component B: wires dependencies and polls for search plans.

Run from the repo root with the virtualenv active:

    python -m search.main

APP_ENV selects the backends (spec §11): the default 'local' uses the
in-memory broker, FakeRedis, FakeExa, and FakeClaude (no paid API calls, and
the empty broker is not shared with intake.main); 'gcp' uses GCP Pub/Sub,
Memorystore, and the production Exa and Claude adapters.
"""

from search.worker import run_worker
from shared import logging as shared_logging
from shared.config import app_env, load_search_settings


def main() -> None:
    """Load Component B settings and run the worker with APP_ENV backends."""
    # SearchSettings never includes the Cloud SQL DSN (spec §8).
    settings = load_search_settings()
    shared_logging.configure(settings.log_level)

    if app_env() == "gcp":
        # Imports live in this branch so a local run never needs the GCP
        # client libraries or working API keys.
        from search.cache import RedisSearchCache
        from search.claude_adapter import LangChainSearchDecider
        from shared.providers.exa import ExaApi
        from shared.pubsub import GcpPubSub

        broker = GcpPubSub(settings.gcp_project)
        # GCP pulls from the subscription; publishing still targets the topic.
        plans_source = settings.pubsub_search_plans_subscription
        cache = RedisSearchCache(settings.redis_host)
        exa = ExaApi(settings.exa_api_key)
        decider = LangChainSearchDecider(settings.anthropic_api_key)
    else:
        from search.cache import FakeRedis
        from shared.messaging import InMemoryBroker
        from shared.providers.claude import FakeClaude
        from shared.providers.exa import FakeExa

        broker = InMemoryBroker()
        # The in-memory broker has no subscriptions; pull straight off the topic.
        plans_source = settings.pubsub_search_plans_topic
        cache = FakeRedis()
        exa = FakeExa()
        decider = FakeClaude()

    run_worker(
        subscriber=broker,
        publisher=broker,
        plans_topic=plans_source,
        candidates_topic=settings.pubsub_candidates_topic,
        cache=cache,
        exa=exa,
        decider=decider,
    )


if __name__ == "__main__":
    main()
