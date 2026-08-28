"""Entry point for Component A: wires dependencies and runs uvicorn.

Run from the repo root with the virtualenv active:

    python -m intake.main

APP_ENV selects the backends (spec §11): the default 'local' uses the
in-memory store/broker and FakeClaude (jobs vanish on restart, no paid API
calls); 'gcp' uses Cloud SQL, GCP Pub/Sub, and the production Claude adapter.
"""

import uvicorn

from intake import api
from shared import logging as shared_logging
from shared.config import app_env, load_settings


def create_app():
    """Wire settings plus APP_ENV-selected backends into the FastAPI app."""
    settings = load_settings()
    shared_logging.configure(settings.log_level)

    # One instance of each backend, shared across requests via overrides.
    if app_env() == "gcp":
        # Imports live in this branch so a local run never needs the GCP
        # client libraries or an Anthropic key that works.
        from intake.claude_adapter import LangChainClaude
        from shared.cloudsql import CloudSqlJobStore
        from shared.pubsub import GcpPubSub

        store = CloudSqlJobStore(settings.cloud_sql_dsn)
        publisher = GcpPubSub(settings.gcp_project)
        claude = LangChainClaude(settings.anthropic_api_key)
    else:
        from shared.db import InMemoryJobStore
        from shared.messaging import InMemoryBroker
        from shared.providers.claude import FakeClaude

        store = InMemoryJobStore()
        publisher = InMemoryBroker()
        claude = FakeClaude()

    # Tell FastAPI which shared objects to provide to the API dependencies.
    api.app.dependency_overrides = {
        api.get_store: lambda: store,
        api.get_publisher: lambda: publisher,
        api.get_claude: lambda: claude,
        api.get_topic: lambda: settings.pubsub_search_plans_topic,
    }
    return api.app


if __name__ == "__main__":
    # 127.0.0.1 in production too: the intake API is never public and is
    # reached through an IAP/SSH tunnel to the VM (spec §15).
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
