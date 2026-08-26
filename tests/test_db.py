"""In-memory job store lifecycle and report idempotency."""

import pytest

from shared.bounds import JobStatus
from shared.db import InMemoryJobStore, JobNotFoundError
from tests.conftest import report


def test_create_job_starts_analyzing():
    store = InMemoryJobStore()
    job_id = store.create_job()
    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.ANALYZING
    assert job.error_code is None


def test_set_status_updates_existing_job():
    store = InMemoryJobStore()
    job_id = store.create_job()
    store.set_status(job_id, JobStatus.FAILED, error_code="provider_error")
    job = store.get_job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "provider_error"


def test_unknown_job_raises_not_found():
    store = InMemoryJobStore()
    with pytest.raises(JobNotFoundError):
        store.set_status("missing", JobStatus.COMPLETED)
    with pytest.raises(JobNotFoundError):
        store.save_report("missing", report())


def test_second_save_report_is_ignored():
    store = InMemoryJobStore()
    job_id = store.create_job()
    first = report(job_id=job_id)
    store.save_report(job_id, first)
    store.save_report(job_id, report(job_id=job_id, uncertainty_notes=["later"]))
    stored = store.get_report(job_id)
    assert stored == first
    assert stored.uncertainty_notes == []
