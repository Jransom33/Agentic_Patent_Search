"""Entry point for Component C: wires dependencies and polls for candidate batches.

Run from the repo root with the virtualenv active:

    python -m report.main

APP_ENV selects the backends (spec §11): the default 'local' uses the
in-memory broker, InMemoryJobStore, FakeExa, and FakeClaude (nothing durable,
no paid API calls, and the empty broker is not shared with search.main);
'gcp' uses GCP Pub/Sub, Cloud SQL, and the production Exa and Claude adapters.
"""

from report.worker import run_worker
from shared import logging as shared_logging
from shared.config import app_env, load_settings


def main() -> None:
    """Load Component C settings and run the worker with APP_ENV backends."""
    settings = load_settings()
    shared_logging.configure(settings.log_level)

    if app_env() == "gcp":
        # Imports live in this branch so a local run never needs the GCP
        # client libraries or working API keys.
        from report.claude_adapter import LangChainCandidateRanker
        from shared.cloudsql import CloudSqlJobStore
        from shared.providers.exa import ExaApi
        from shared.pubsub import GcpPubSub

        subscriber = GcpPubSub(settings.gcp_project)
        # GCP pulls from the subscription, not the topic.
        candidates_source = settings.pubsub_candidates_subscription
        store = CloudSqlJobStore(settings.cloud_sql_dsn)
        ranker = LangChainCandidateRanker(settings.anthropic_api_key)
        exa = ExaApi(settings.exa_api_key)
    else:
        from shared.db import InMemoryJobStore
        from shared.messaging import InMemoryBroker
        from shared.providers.claude import FakeClaude
        from shared.providers.exa import FakeExa

        subscriber = InMemoryBroker()
        # The in-memory broker has no subscriptions; pull straight off the topic.
        candidates_source = settings.pubsub_candidates_topic
        store = InMemoryJobStore()
        ranker = FakeClaude()
        exa = FakeExa()

    run_worker(
        subscriber=subscriber,
        candidates_topic=candidates_source,
        store=store,
        ranker=ranker,
        exa=exa,
    )


if __name__ == "__main__":
    main()
