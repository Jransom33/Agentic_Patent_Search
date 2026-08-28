"""Component A pipeline: job creation, claim analysis, and plan publishing.

Implements the success/failure path of the spec §7 sequence diagram. The API
layer validates uploads first (extraction.py) and then calls run_intake with
injected JobStore / Publisher / ClaimAnalyzer, so tests can pass the in-memory
fakes. Only bounded lifecycle events are logged — never document text.
"""

import time
from typing import NoReturn

from pydantic import ValidationError

from intake.extraction import ExtractedInputs
from shared.bounds import JobStatus
from shared.db import JobStore
from shared.logging import log_event
from shared.messaging import Publisher
from shared.models import SearchPlanMessage
from shared.providers.claude import ClaimAnalyzer

COMPONENT = "intake"


class IntakeFailedError(Exception):
    """Analysis or publish failed after the job was created.

    Carries the job id and a short safe error code so the API layer can
    report which job failed without exposing provider details.
    """

    def __init__(self, job_id: str, error_code: str, error: str) -> None:
        super().__init__(error_code)
        self.job_id = job_id
        self.error_code = error_code
        self.error = error


def _safe_analysis_error(exc: Exception) -> str:
    """Describe the failure without returning provider or document content."""
    if isinstance(exc, ValidationError):
        item = exc.errors(include_input=False, include_context=False)[0]
        location = ".".join(map(str, item["loc"])) or "response"
        # Keep the generated diagnostic within shared.logging's 80-char field
        # bound so it is visible rather than redacted.
        return f"Claude validation failed: {location}: {item['msg']}"[:80]
    if str(exc) == "Claude did not return a valid structured claim analysis":
        return str(exc)
    return f"Analysis failed ({type(exc).__name__})"


def _fail(
    store: JobStore, job_id: str, error_code: str, error: str
) -> NoReturn:
    # Shared failure path: persist the failed state, log the safe code, and
    # abort the pipeline. error_code is a short token, never an exception
    # message that might contain document or provider text.
    store.set_status(job_id, JobStatus.FAILED, error_code=error_code)
    log_event(
        component=COMPONENT,
        event="job_failed",
        job_id=job_id,
        error_code=error_code,
        error_detail=error,
    )
    raise IntakeFailedError(job_id, error_code, error)


def run_intake(
    inputs: ExtractedInputs,
    *,
    store: JobStore,
    publisher: Publisher,
    claude: ClaimAnalyzer,
    topic: str,
) -> str:
    """Create a job, analyze the claims, publish the plan, return the job id.

    Raises IntakeFailedError (with the job already marked failed) if Claude
    analysis, plan validation, or publishing fails.
    """
    started = time.monotonic()

    # Create the job first so a failure at any later step has a persistent
    # record the user can poll (spec §11: no indefinitely running jobs).
    job_id = store.create_job()
    log_event(component=COMPONENT, event="job_created", job_id=job_id)

    try:
        # Claude proposes the claim map and queries; building SearchPlanMessage
        # from its output runs the shared validators (12-query cap, unique ids,
        # limitation links) that ClaimAnalysis alone does not enforce.
        analysis = claude.analyze_claims(
            inputs.spec_text, inputs.claims_text, inputs.critical_date
        )
        plan = SearchPlanMessage(
            job_id=job_id,
            critical_date=inputs.critical_date,
            limitations=analysis.limitations,
            concepts=analysis.concepts,
            queries=analysis.queries,
        )
    except Exception as exc:
        # ASSUMPTION: one code covers both a provider error and an invalid
        # structured response; the log event is enough to tell them apart later.
        # FOLLOW-UP: spec §11 suggests bounded retries (MAX_RETRIES) for
        # temporary provider failures; this version fails on the first error.
        _fail(store, job_id, "analysis_failed", _safe_analysis_error(exc))

    try:
        publisher.publish(topic, plan)
    except Exception as exc:
        _fail(
            store,
            job_id,
            "publish_failed",
            f"Publishing failed ({type(exc).__name__})",
        )

    # Publish confirmed: hand the job to Component B and report the id.
    store.set_status(job_id, JobStatus.SEARCHING)
    log_event(
        component=COMPONENT,
        event="plan_published",
        job_id=job_id,
        duration_ms=(time.monotonic() - started) * 1000,
    )
    return job_id
