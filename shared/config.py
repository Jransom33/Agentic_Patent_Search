"""Load settings from the environment. Secret values must never appear in logs."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Full set for Components A and C (they persist jobs/reports in Cloud SQL).
REQUIRED_NAMES = (
    "GCP_PROJECT",
    "PUBSUB_SEARCH_PLANS_TOPIC",
    "PUBSUB_CANDIDATES_TOPIC",
    "CLOUD_SQL_DSN",
    "REDIS_HOST",
    "ANTHROPIC_API_KEY",
    "EXA_API_KEY",
)
# Component B must not receive Cloud SQL credentials (spec §8).
SEARCH_REQUIRED_NAMES = tuple(name for name in REQUIRED_NAMES if name != "CLOUD_SQL_DSN")


def _read_required(names: tuple[str, ...]) -> dict[str, str]:
    """Load optional .env, then require every name. Existing process env wins."""
    # UNCERTAIN: load_dotenv() searches cwd; GCP VMs may use real env vars only.
    load_dotenv()
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("missing required environment variables: " + ", ".join(missing))
    return values


def _log_level() -> str:
    # UNCERTAIN: not checked against DEBUG/INFO/WARNING/ERROR.
    return os.environ.get("LOG_LEVEL", "INFO").strip() or "INFO"


@dataclass(frozen=True)
class Settings:
    gcp_project: str
    pubsub_search_plans_topic: str
    pubsub_candidates_topic: str
    cloud_sql_dsn: str
    redis_host: str
    anthropic_api_key: str
    exa_api_key: str
    log_level: str

    # Print names and non-secrets only. Keys and the DB DSN stay masked.
    # INCOMPLETE: attribute access (settings.exa_api_key) still returns the real
    # value; logging must not interpolate those fields.
    def __repr__(self) -> str:
        return (
            "Settings("
            f"gcp_project={self.gcp_project!r}, "
            f"pubsub_search_plans_topic={self.pubsub_search_plans_topic!r}, "
            f"pubsub_candidates_topic={self.pubsub_candidates_topic!r}, "
            "cloud_sql_dsn='***', "
            f"redis_host={self.redis_host!r}, "
            "anthropic_api_key='***', "
            "exa_api_key='***', "
            f"log_level={self.log_level!r})"
        )


@dataclass(frozen=True)
class SearchSettings:
    """Component B settings: Pub/Sub, Redis, Exa, Anthropic, logging. No Cloud SQL."""

    gcp_project: str
    pubsub_search_plans_topic: str
    pubsub_candidates_topic: str
    redis_host: str
    anthropic_api_key: str
    exa_api_key: str
    log_level: str

    def __repr__(self) -> str:
        return (
            "SearchSettings("
            f"gcp_project={self.gcp_project!r}, "
            f"pubsub_search_plans_topic={self.pubsub_search_plans_topic!r}, "
            f"pubsub_candidates_topic={self.pubsub_candidates_topic!r}, "
            f"redis_host={self.redis_host!r}, "
            "anthropic_api_key='***', "
            "exa_api_key='***', "
            f"log_level={self.log_level!r})"
        )


def load_settings() -> Settings:
    """Load the full A/C settings, including the Cloud SQL DSN."""
    values = _read_required(REQUIRED_NAMES)
    return Settings(
        gcp_project=values["GCP_PROJECT"],
        pubsub_search_plans_topic=values["PUBSUB_SEARCH_PLANS_TOPIC"],
        pubsub_candidates_topic=values["PUBSUB_CANDIDATES_TOPIC"],
        cloud_sql_dsn=values["CLOUD_SQL_DSN"],
        redis_host=values["REDIS_HOST"],
        anthropic_api_key=values["ANTHROPIC_API_KEY"],
        exa_api_key=values["EXA_API_KEY"],
        log_level=_log_level(),
    )


def load_search_settings() -> SearchSettings:
    """Load Component B settings. Succeeds even when CLOUD_SQL_DSN is unset."""
    values = _read_required(SEARCH_REQUIRED_NAMES)
    return SearchSettings(
        gcp_project=values["GCP_PROJECT"],
        pubsub_search_plans_topic=values["PUBSUB_SEARCH_PLANS_TOPIC"],
        pubsub_candidates_topic=values["PUBSUB_CANDIDATES_TOPIC"],
        redis_host=values["REDIS_HOST"],
        anthropic_api_key=values["ANTHROPIC_API_KEY"],
        exa_api_key=values["EXA_API_KEY"],
        log_level=_log_level(),
    )
