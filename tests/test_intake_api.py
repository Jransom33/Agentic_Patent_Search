"""Component A HTTP tests. Uses in-memory fakes; never calls Anthropic."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from intake import api
from shared.bounds import MAX_UPLOAD_BYTES, JobStatus
from shared.db import InMemoryJobStore
from shared.messaging import InMemoryBroker
from shared.models import SearchPlanMessage
from shared.providers.claude import FakeClaude
from tests.conftest import report
from tests.test_intake_extraction import blank_pdf_bytes, text_pdf_bytes

TOPIC = "search-plans"


@pytest.fixture
def intake():
    """Wire one store/broker/fake Claude into the app for the length of a test."""
    store = InMemoryJobStore()
    broker = InMemoryBroker()
    api.app.dependency_overrides = {
        api.get_store: lambda: store,
        api.get_publisher: lambda: broker,
        api.get_claude: lambda: FakeClaude(),
        api.get_topic: lambda: TOPIC,
    }
    client = TestClient(api.app)
    yield client, store, broker
    api.app.dependency_overrides.clear()


def _submit(client, pdf=None, claims=b"1. A widget.", critical_date="2020-01-01"):
    return client.post(
        "/jobs",
        files={
            "specification": ("spec.pdf", pdf if pdf is not None else text_pdf_bytes()),
            "claims": ("claims.txt", claims),
        },
        data={"critical_date": critical_date},
    )


def test_valid_submit_returns_job_and_publishes_plan(intake):
    """Submit valid files; expect a search job and plan so Component B can start."""
    client, store, broker = intake
    response = _submit(client)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.json()["status"] == JobStatus.SEARCHING
    assert store.get_job(job_id).status == JobStatus.SEARCHING

    # The published plan is the FakeClaude claim map plus this job id.
    plan = broker.receive(TOPIC, SearchPlanMessage)
    assert plan is not None
    assert plan.job_id == job_id
    assert plan.critical_date == date(2020, 1, 1)
    assert plan.queries[0].limitation_ids == ["L1"]


def test_poll_returns_searching_status(intake):
    """Poll a newly submitted job; expect its in-progress status without a report."""
    client, _, _ = intake
    job_id = _submit(client).json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body == {"job_id": job_id, "status": JobStatus.SEARCHING}


def test_poll_returns_stored_report_when_completed(intake):
    """Poll a completed job with a stored report; expect both status and report data."""
    client, store, _ = intake
    job_id = _submit(client).json()["job_id"]
    stored = report(job_id=job_id, critical_date=date(2020, 1, 1))
    store.save_report(job_id, stored)
    store.set_status(job_id, JobStatus.COMPLETED)

    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == JobStatus.COMPLETED
    assert body["report"]["job_id"] == job_id
    assert body["report"]["evidence"][0]["rank"] == 1


def test_unknown_job_returns_404(intake):
    """Request a job ID that was never created; expect 404 instead of a false status."""
    client, _, _ = intake
    assert client.get("/jobs/missing").status_code == 404


@pytest.mark.parametrize(
    "pdf,claims,date_text,snippet",
    [
        (b"not a pdf", b"1. A widget.", "2020-01-01", "not a readable PDF"),
        (None, b"1. A widget.", "20200101", "YYYY-MM-DD"),
        (None, b"\xff\xfe claims", "2020-01-01", "not valid UTF-8"),
    ],
)
def test_invalid_inputs_return_400(intake, pdf, claims, date_text, snippet):
    """Submit one malformed input; expect 400 before a job or search plan is created."""
    client, store, broker = intake
    pdf_bytes = text_pdf_bytes() if pdf is None else pdf
    response = _submit(client, pdf=pdf_bytes, claims=claims, critical_date=date_text)
    assert response.status_code == 400
    assert snippet in response.json()["detail"]
    # Bad input is rejected before a job exists, so nothing was published.
    assert broker.receive(TOPIC, SearchPlanMessage) is None
    assert store._jobs == {}


def test_image_only_pdf_returns_400(intake):
    """Submit a PDF without embedded text; expect 400 because OCR is out of scope."""
    client, _, _ = intake
    response = _submit(client, pdf=blank_pdf_bytes())
    assert response.status_code == 400
    assert "no embedded text" in response.json()["detail"]


def test_oversized_upload_returns_400(intake):
    """Submit a file over the configured limit; expect 400 before it is parsed."""
    client, _, _ = intake
    response = _submit(client, pdf=b"x" * (MAX_UPLOAD_BYTES + 1))
    assert response.status_code == 400
    assert "size limit" in response.json()["detail"]


class _FailingClaude:
    def analyze_claims(self, spec_text, claims_text, critical_date):
        raise RuntimeError("provider down")


class _FailingPublisher:
    def publish(self, topic, model):
        raise RuntimeError("pubsub down")


def test_claude_failure_marks_job_failed(intake):
    """Make claim analysis fail; expect a pollable failed job and no published plan."""
    client, store, broker = intake
    api.app.dependency_overrides[api.get_claude] = lambda: _FailingClaude()
    response = _submit(client)
    assert response.status_code == 502
    job_id = response.json()["detail"]["job_id"]
    assert response.json()["detail"]["error_code"] == "analysis_failed"
    assert store.get_job(job_id).status == JobStatus.FAILED
    assert broker.receive(TOPIC, SearchPlanMessage) is None

    polled = client.get(f"/jobs/{job_id}").json()
    assert polled["status"] == JobStatus.FAILED
    assert polled["error_code"] == "analysis_failed"


def test_publish_failure_marks_job_failed(intake):
    """Make plan publishing fail; expect a pollable failed job without a report."""
    client, store, _ = intake
    api.app.dependency_overrides[api.get_publisher] = lambda: _FailingPublisher()
    response = _submit(client)
    assert response.status_code == 502
    job_id = response.json()["detail"]["job_id"]
    assert response.json()["detail"]["error_code"] == "publish_failed"
    assert store.get_job(job_id).status == JobStatus.FAILED
    assert "report" not in client.get(f"/jobs/{job_id}").json()
