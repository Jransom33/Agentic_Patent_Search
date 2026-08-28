"""Cloud SQL for PostgreSQL implementation of the JobStore interface.

Spec §11: executes the shared schema on startup, keeps save_report
first-write-wins, and stores the report and completed status in one database
transaction. Tests keep using InMemoryJobStore; this module is only wired in
production, over private networking per spec §15.
"""

import uuid

import psycopg

from shared.bounds import JobStatus
from shared.db import SCHEMA_SQL, JobNotFoundError, JobRecord
from shared.models import Report

# Shared by save_report and complete_job so both stay first-write-wins.
# The ::jsonb cast stores the pydantic JSON string as a queryable JSONB value.
_INSERT_REPORT_SQL = (
    "INSERT INTO reports (job_id, report_json) VALUES (%s, %s::jsonb) "
    "ON CONFLICT (job_id) DO NOTHING"
)


class CloudSqlJobStore:
    """JobStore backed by Cloud SQL for PostgreSQL, connected by DSN."""

    def __init__(self, dsn: str) -> None:
        """Keep the DSN and create the jobs/reports tables if missing.

        SCHEMA_SQL only uses CREATE TABLE IF NOT EXISTS, so components A and
        C can both run it safely at startup in any order.
        """
        self._dsn = dsn
        with self._connect() as conn:
            conn.execute(SCHEMA_SQL)

    def _connect(self) -> psycopg.Connection:
        """Open one connection whose with-block commits or rolls back.

        One connection per operation makes every JobStore call its own
        transaction and avoids babysitting a long-lived connection that
        Cloud SQL may drop when idle.
        """
        # UNCERTAIN: no pooling and no explicit connect timeout. Fine for the
        # class demo's low traffic; put connect_timeout in the DSN if needed.
        return psycopg.connect(self._dsn)

    def create_job(self) -> str:
        """Insert a job in analyzing and return its id."""
        # Same uuid4-hex ids as InMemoryJobStore so both stores look alike.
        job_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status) VALUES (%s, %s)",
                (job_id, JobStatus.ANALYZING),
            )
        return job_id

    def set_status(
        self, job_id: str, status: JobStatus, error_code: str | None = None
    ) -> None:
        """Update lifecycle status. error_code is for failed jobs only."""
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE jobs SET status = %s, error_code = %s, updated_at = NOW() "
                "WHERE job_id = %s",
                (status, error_code, job_id),
            )
            if updated.rowcount == 0:
                raise JobNotFoundError(job_id)

    def get_job(self, job_id: str) -> JobRecord | None:
        """Return the job row, or None if this id was never created."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, error_code FROM jobs WHERE job_id = %s", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return JobRecord(job_id=job_id, status=JobStatus(row[0]), error_code=row[1])

    def save_report(self, job_id: str, report: Report) -> None:
        """Store the report once. A second save for the same job is ignored.

        ON CONFLICT DO NOTHING makes duplicate Pub/Sub deliveries harmless;
        an unknown job id trips the foreign key and maps to JobNotFoundError.
        """
        try:
            with self._connect() as conn:
                conn.execute(_INSERT_REPORT_SQL, (job_id, report.model_dump_json()))
        except psycopg.errors.ForeignKeyViolation:
            raise JobNotFoundError(job_id) from None

    def get_report(self, job_id: str) -> Report | None:
        """Return the stored report, or None if Component C has not saved one."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM reports WHERE job_id = %s", (job_id,)
            ).fetchone()
        if row is None:
            return None
        # psycopg parses JSONB to a dict; validation rebuilds the full model.
        return Report.model_validate(row[0])

    def complete_job(self, job_id: str, report: Report) -> None:
        """Store the report and mark the job completed in one transaction.

        Both statements run inside a single connection with-block, so either
        both commit or both roll back (spec §11). A crash can therefore never
        strand a stored report on a job still marked ranking.
        """
        try:
            with self._connect() as conn:
                conn.execute(_INSERT_REPORT_SQL, (job_id, report.model_dump_json()))
                updated = conn.execute(
                    "UPDATE jobs SET status = %s, error_code = NULL, "
                    "updated_at = NOW() WHERE job_id = %s",
                    (JobStatus.COMPLETED, job_id),
                )
                # Raising inside the with-block rolls the insert back too.
                if updated.rowcount == 0:
                    raise JobNotFoundError(job_id)
        except psycopg.errors.ForeignKeyViolation:
            raise JobNotFoundError(job_id) from None
