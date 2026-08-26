"""Lifecycle log redaction for document text and secrets."""

import logging

from shared.logging import MAX_FIELD_CHARS, log_event


def _logged(caplog) -> str:
    caplog.set_level(logging.INFO, logger="shared")
    return caplog


def test_normal_fields_pass_through(caplog):
    _logged(caplog)
    log_event(component="intake", event="analyzing", job_id="job1", duration_ms=12)
    assert "component=intake" in caplog.text
    assert "event=analyzing" in caplog.text
    assert "job_id=job1" in caplog.text
    assert "duration_ms=12" in caplog.text


def test_multiline_is_redacted(caplog):
    _logged(caplog)
    log_event(component="intake", event="line1\nline2")
    assert "event=[redacted]" in caplog.text


def test_oversized_field_is_redacted(caplog):
    _logged(caplog)
    log_event(component="intake", event="x" * (MAX_FIELD_CHARS + 1))
    assert "event=[redacted]" in caplog.text


def test_credential_pattern_is_redacted(caplog):
    _logged(caplog)
    log_event(component="intake", event="sk-ant-example")
    assert "event=[redacted]" in caplog.text


def test_configured_secret_env_is_redacted(caplog, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret-value")
    _logged(caplog)
    log_event(component="intake", event="test-secret-value")
    assert "event=[redacted]" in caplog.text
    assert "test-secret-value" not in caplog.text
