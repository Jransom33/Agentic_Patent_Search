"""Local entry point for Component A: wires dependencies and runs uvicorn.

Run from the repo root with the virtualenv active:

    python -m intake.main

# INCOMPLETE: local runs use the in-memory store/broker and FakeClaude, so
# jobs vanish on restart and no real APIs are called. Production wiring
# (Cloud SQL, GCP Pub/Sub, the real Claude adapter) lands with spec §11.
"""

import uvicorn

from intake import api
from shared import logging as shared_logging
from shared.config import load_settings
from shared.db import InMemoryJobStore
from shared.messaging import InMemoryBroker
from shared.providers.claude import FakeClaude


def create_app():
    """Wire real settings and (for now) in-memory backends into the app."""
    # Settings still come from the environment so the topic name and log
    # level match what the deployed system will use.
    settings = load_settings()
    shared_logging.configure(settings.log_level)

    # One instance of each backend, shared across requests via overrides.
    store = InMemoryJobStore()
    broker = InMemoryBroker()
    claude = FakeClaude()
    # Tell FastAPI which shared local objects to provide to the API dependencies.
    api.app.dependency_overrides = {
        api.get_store: lambda: store,
        api.get_publisher: lambda: broker,
        api.get_claude: lambda: claude,
        api.get_topic: lambda: settings.pubsub_search_plans_topic,
    }
    return api.app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
