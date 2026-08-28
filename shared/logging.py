"""Structured logs for all three components.

log_event emits bounded lifecycle fields with redaction (spec §12).
log_verbose emits full prompt/document/response text, masking only secrets.
"""

import logging
import os
import re

# Short tokens only. Anything longer is treated as document/payload dump.
# ASSUMPTION: 80 chars is enough for event names and error codes, and short
# enough that a claim paragraph cannot sneak through.
MAX_FIELD_CHARS = 80
# INCOMPLETE: Redis AUTH strings are not in this list. Add them if Redis
# is password-protected later.
_SECRET_ENV_NAMES = ("ANTHROPIC_API_KEY", "EXA_API_KEY", "CLOUD_SQL_DSN")
# UNCERTAIN: these patterns will not catch every Exa key format.
_SECRET_RE = re.compile(r"(sk-ant-|postgresql://|password=|api[_-]?key)", re.I)

_logger = logging.getLogger("shared")


def configure(level: str = "INFO") -> None:
    # UNCERTAIN: basicConfig is a no-op if the root logger already has handlers
    # (e.g. uvicorn). Components may need to attach a handler themselves.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(message)s",
    )


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "\n" in text or len(text) > MAX_FIELD_CHARS or _SECRET_RE.search(text):
        return "[redacted]"
    for secret in (os.environ.get(name, "") for name in _SECRET_ENV_NAMES):
        if secret and secret in text:
            return "[redacted]"
    return text


def log_event(
    *,
    component: str,
    event: str,
    job_id: str | None = None,
    duration_ms: int | float | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    # Only these bounded fields are allowed. Callers cannot pass prompt/text
    # extras; error_detail is intended for already-sanitized diagnostics.
    # FOLLOW-UP: Component A/B/C should call this at each lifecycle step
    # (analyzing, searching, ranking, completed, failed).
    parts = [
        f"component={_clean(component) or '[redacted]'}",
        f"event={_clean(event) or '[redacted]'}",
    ]
    cleaned_job = _clean(job_id)
    if cleaned_job is not None:
        parts.append(f"job_id={cleaned_job}")
    if duration_ms is not None:
        parts.append(f"duration_ms={int(duration_ms)}")
    cleaned_error = _clean(error_code)
    if cleaned_error is not None:
        parts.append(f"error_code={cleaned_error}")
    cleaned_detail = _clean(error_detail)
    if cleaned_detail is not None:
        parts.append(f"error_detail={cleaned_detail}")
    _logger.info(" ".join(parts))


def log_verbose(component: str, event: str, text: str) -> None:
    """Log full text at INFO, replacing secrets with [key].

    Unlike log_event, this does not truncate or redact document content.
    Only known env secrets and sk-ant- tokens are masked.
    """
    masked = str(text)
    for secret in (os.environ.get(name, "") for name in _SECRET_ENV_NAMES):
        if secret:
            masked = masked.replace(secret, "[key]")
    masked = re.sub(r"sk-ant-\S+", "[key]", masked)
    _logger.info("component=%s event=%s\n%s", component, event, masked)
