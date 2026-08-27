"""Component A FastAPI app: submit a job, poll for status or the report.

The app never talks to real backends directly. It resolves JobStore,
Publisher, ClaimAnalyzer, and the topic name through FastAPI dependencies,
which main.py wires for local runs and tests override with fakes.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile

from intake.extraction import InputValidationError, validate_and_extract
from intake.pipeline import IntakeFailedError, run_intake
from shared.bounds import JobStatus
from shared.db import JobStore
from shared.messaging import Publisher
from shared.providers.claude import ClaimAnalyzer

app = FastAPI(title="Prior Art Intake (Component A)")


# --- Dependency placeholders -------------------------------------------------
# These raise until wiring replaces them via app.dependency_overrides. That
# keeps this module import-safe with no backend choices baked in.

def get_store() -> JobStore:
    raise RuntimeError("JobStore dependency is not wired")


def get_publisher() -> Publisher:
    raise RuntimeError("Publisher dependency is not wired")


def get_claude() -> ClaimAnalyzer:
    raise RuntimeError("ClaimAnalyzer dependency is not wired")


def get_topic() -> str:
    raise RuntimeError("search-plans topic dependency is not wired")


# --- Endpoints ----------------------------------------------------------------

@app.post("/jobs", status_code=202)
async def submit_job(
    specification: UploadFile,
    claims: UploadFile,
    critical_date: Annotated[str, Form()],
    store: Annotated[JobStore, Depends(get_store)],
    publisher: Annotated[Publisher, Depends(get_publisher)],
    claude: Annotated[ClaimAnalyzer, Depends(get_claude)],
    topic: Annotated[str, Depends(get_topic)],
) -> dict:
    """Validate the uploads, run the intake pipeline, and return the job id."""
    # Read both uploads into memory only; nothing is written to disk (spec §7).
    # ASSUMPTION: reading before the size check is acceptable for a class
    # project; a streaming size guard would reject oversized bodies earlier.
    pdf_bytes = await specification.read()
    claims_bytes = await claims.read()

    try:
        # extraction.py raises InputValidationError with client-safe messages
        # only, so echoing the message in the 400 detail leaks no document text.
        inputs = validate_and_extract(pdf_bytes, claims_bytes, critical_date)
    except InputValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        job_id = run_intake(
            inputs, store=store, publisher=publisher, claude=claude, topic=topic
        )
    except IntakeFailedError as exc:
        # The job is already marked failed; tell the client which job so it
        # can still be polled. 502: our upstream step failed, not their input.
        raise HTTPException(
            status_code=502,
            detail={"job_id": exc.job_id, "error_code": exc.error_code},
        )

    return {"job_id": job_id, "status": JobStatus.SEARCHING}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> dict:
    """Return the job's current status, or the report once it is completed."""
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")

    # Include the report only when Component C has stored one; polling before
    # completion returns just the visible lifecycle state (spec §11).
    response: dict = {"job_id": record.job_id, "status": record.status}
    if record.error_code is not None:
        response["error_code"] = record.error_code
    if record.status == JobStatus.COMPLETED:
        report = store.get_report(job_id)
        # UNCERTAIN: a completed job with no stored report should not happen;
        # returning report=None instead of a 500 keeps polling safe.
        response["report"] = report.model_dump(mode="json") if report else None
    return response
