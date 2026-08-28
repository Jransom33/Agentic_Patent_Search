"""Settings loading and secret masking."""

import pytest

from shared.config import (
    REQUIRED_NAMES,
    SEARCH_REQUIRED_NAMES,
    load_search_settings,
    load_settings,
)

_ENV = {
    "GCP_PROJECT": "demo-project",
    "PUBSUB_SEARCH_PLANS_TOPIC": "search-plans",
    "PUBSUB_SEARCH_PLANS_SUBSCRIPTION": "search-plans-sub",
    "PUBSUB_CANDIDATES_TOPIC": "candidates",
    "PUBSUB_CANDIDATES_SUBSCRIPTION": "candidates-sub",
    "CLOUD_SQL_DSN": "postgresql://user:secret@localhost/db",
    "REDIS_HOST": "localhost",
    "ANTHROPIC_API_KEY": "sk-ant-test-key",
    "EXA_API_KEY": "exa-test-key",
}


def _clear_required(monkeypatch):
    """Ignore .env and drop required vars so tests control the environment."""
    monkeypatch.setattr("shared.config.load_dotenv", lambda: None)
    for name in set(REQUIRED_NAMES + SEARCH_REQUIRED_NAMES):
        monkeypatch.delenv(name, raising=False)


def test_load_settings_reads_full_env(monkeypatch):
    _clear_required(monkeypatch)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert settings.gcp_project == "demo-project"
    assert settings.pubsub_candidates_subscription == "candidates-sub"
    assert settings.anthropic_api_key == "sk-ant-test-key"


def test_load_settings_names_every_missing_variable(monkeypatch):
    _clear_required(monkeypatch)
    with pytest.raises(ValueError) as exc:
        load_settings()
    message = str(exc.value)
    for name in REQUIRED_NAMES:
        assert name in message


def test_repr_masks_keys_and_dsn(monkeypatch):
    _clear_required(monkeypatch)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    text = repr(load_settings())
    assert "cloud_sql_dsn='***'" in text
    assert "anthropic_api_key='***'" in text
    assert "exa_api_key='***'" in text
    assert "secret" not in text
    assert "sk-ant-test-key" not in text
    assert "exa-test-key" not in text


def test_search_settings_load_without_cloud_sql(monkeypatch):
    """Component B must start without database credentials (spec §8)."""
    _clear_required(monkeypatch)
    for name, value in _ENV.items():
        if name != "CLOUD_SQL_DSN":
            monkeypatch.setenv(name, value)
    settings = load_search_settings()
    assert settings.redis_host == "localhost"
    assert settings.pubsub_search_plans_subscription == "search-plans-sub"
    assert not hasattr(settings, "cloud_sql_dsn")
    text = repr(settings)
    assert "anthropic_api_key='***'" in text
    assert "sk-ant-test-key" not in text
