"""Local entry point for Component C: wires fakes and polls for candidate batches.

Run from the repo root with the virtualenv active:

    python -m report.main

INCOMPLETE: local runs use the in-memory broker, InMemoryJobStore, FakeExa,
and FakeClaude, so nothing is durable and no real APIs are called. Production
wiring (GCP Pub/Sub, Cloud SQL, the real Claude/Exa adapters) lands with
spec §11.
"""

from report.worker import run_worker
from shared import logging as shared_logging
from shared.config import load_settings
from shared.db import InMemoryJobStore
from shared.messaging import InMemoryBroker
from shared.providers.claude import FakeClaude
from shared.providers.exa import FakeExa


def main() -> None:
    """Load Component C settings and run the worker with local fakes."""
    # load_settings includes CLOUD_SQL_DSN even though this local process
    # does not open a database connection.
    # ASSUMPTION: FakeClaude.rank_candidates is enough for a local smoke run.
    # UNCERTAIN: this process's InMemoryBroker and InMemoryJobStore start
    # empty and are not shared with intake.main or search.main, so a local
    # end-to-end run needs a later shared broker/store or real Pub/Sub + SQL.
    settings = load_settings()
    shared_logging.configure(settings.log_level)
    run_worker(
        subscriber=InMemoryBroker(),
        candidates_topic=settings.pubsub_candidates_topic,
        store=InMemoryJobStore(),
        ranker=FakeClaude(),
        exa=FakeExa(),
    )


if __name__ == "__main__":
    main()
