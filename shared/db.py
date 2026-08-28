"""Job and report storage used by Components A and C.

No uploaded specification or claims text is stored. Tests and local runs use
InMemoryJobStore; production uses shared.cloudsql.CloudSqlJobStore.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from shared.bounds import JobStatus
from shared.models import Report

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reports (
    job_id TEXT PRIMARY KEY REFERENCES jobs (job_id),
    report_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
# CloudSqlJobStore executes this idempotent SQL on startup. InMemoryJobStore
# does not use these tables.


class JobNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    status: JobStatus
    error_code: str | None = None


class JobStore(Protocol):
    """Interface Components A and C use to create jobs and store reports.

    InMemoryJobStore implements this for tests and CloudSqlJobStore for
    production.
    """
    def create_job(self) -> str: ...
    def set_status(
        self, job_id: str, status: JobStatus, error_code: str | None = None
    ) -> None: ...
    def get_job(self, job_id: str) -> JobRecord | None: ...
    def save_report(self, job_id: str, report: Report) -> None: ...
    def get_report(self, job_id: str) -> Report | None: ...
    def complete_job(self, job_id: str, report: Report) -> None: ...


class InMemoryJobStore:
    """Dict-backed stand-in for Cloud SQL. Nothing is written to disk."""

    def __init__(self) -> None:
        """Start with no jobs and no reports."""
        self._jobs: dict[str, JobRecord] = {}
        self._reports: dict[str, Report] = {}

    def create_job(self) -> str:
        """Insert a job in analyzing and return its id."""
        # ASSUMPTION: uuid4 hex (32 chars) is a fine job_id; not a UUID with dashes.
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = JobRecord(job_id=job_id, status=JobStatus.ANALYZING)
        return job_id

    def set_status(
        self, job_id: str, status: JobStatus, error_code: str | None = None
    ) -> None:
        """Update lifecycle status. error_code is for failed jobs only."""
        # FOLLOW-UP: error_code is not length-checked; callers must pass a short
        # safe code, not an exception message that might contain document text.
        if job_id not in self._jobs:
            raise JobNotFoundError(job_id)
        self._jobs[job_id] = JobRecord(
            job_id=job_id, status=status, error_code=error_code
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        """Return the job row, or None if this id was never created."""
        return self._jobs.get(job_id)

    def save_report(self, job_id: str, report: Report) -> None:
        """Store the report once. A second save for the same job is ignored.

        Duplicate Pub/Sub deliveries must not create a second report.
        """
        # ASSUMPTION: first write wins; a later different report is dropped.
        # UNCERTAIN: does not check report.job_id == job_id; callers pass both
        # from the same batch today.
        if job_id not in self._jobs:
            raise JobNotFoundError(job_id)
        if job_id in self._reports:
            return
        self._reports[job_id] = report

    def get_report(self, job_id: str) -> Report | None:
        """Return the stored report, or None if Component C has not saved one."""
        return self._reports.get(job_id)

    def complete_job(self, job_id: str, report: Report) -> None:
        """Store the report and mark the job completed as one step (spec §11).

        In memory two dict writes cannot be observed half-done by this
        single-threaded process; only Cloud SQL needs a real transaction.
        """
        self.save_report(job_id, report)
        self.set_status(job_id, JobStatus.COMPLETED)
