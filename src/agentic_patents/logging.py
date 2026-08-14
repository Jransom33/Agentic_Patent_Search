"""Safe structured logs for all three components.

Each line has component, job_id, lifecycle event, duration_ms, and error_code
(spec §11). Values that look like document text, prompts, or credentials are
replaced with [redacted] (spec §12).
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

_logger = logging.getLogger("agentic_patents")


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
) -> None:
    # Only these fields are allowed. Callers cannot pass prompt/text extras.
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
    _logger.info(" ".join(parts))
