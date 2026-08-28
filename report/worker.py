"""Component C worker: consume candidate batches, rank, and store reports.

Implements the spec §10 sequence around the pipeline: skip jobs that already
have a report, mark Component B terminal failures, otherwise rank and persist.
The worker acks its input only after handle_batch succeeds, so a crash or
failure before the report is stored leads to redelivery, not a lost batch.
"""

import time

from report.pipeline import RankingFailedError, run_ranking
from shared.bounds import JobStatus
from shared.db import JobStore
from shared.logging import log_event
from shared.messaging import Subscriber
from shared.models import CandidateBatchMessage
from shared.providers.claude import CandidateRanker
from shared.providers.exa import ExaClient

COMPONENT = "report"
# How long to wait when the local fake broker has nothing queued.
# ASSUMPTION: 1s is fine for a class-demo poll loop; GCP Pub/Sub will block.
_IDLE_SLEEP_SECONDS = 1.0


def handle_batch(
    batch: CandidateBatchMessage,
    *,
    store: JobStore,
    ranker: CandidateRanker,
    exa: ExaClient,
) -> None:
    """Process one CandidateBatchMessage through to a stored report or failure.

    Duplicate deliveries (a report already exists) return without ranking.
    Does not ack the input; the caller / GCP adapter does that after this
    returns. Store failures raise so the input is not treated as done.
    """
    started = time.monotonic()
    job_id = batch.plan.job_id

    # Duplicate Pub/Sub delivery: ranking already produced a stored report.
    # complete_job stores the report and completed status atomically, so a
    # skipped duplicate can never find a stored report on a job still ranking.
    if store.get_report(job_id) is not None:
        log_event(component=COMPONENT, event="duplicate_acked", job_id=job_id)
        return

    # Component B published a sanitized terminal failure with no candidates.
    if batch.error_code is not None:
        store.set_status(job_id, JobStatus.FAILED, error_code=batch.error_code)
        log_event(
            component=COMPONENT,
            event="job_failed",
            job_id=job_id,
            error_code=batch.error_code,
        )
        return

    store.set_status(job_id, JobStatus.RANKING)
    log_event(component=COMPONENT, event="ranking_started", job_id=job_id)
    try:
        report = run_ranking(batch, ranker=ranker, exa=exa)
    except RankingFailedError as exc:
        store.set_status(job_id, JobStatus.FAILED, error_code=exc.error_code)
        log_event(
            component=COMPONENT,
            event="job_failed",
            job_id=job_id,
            error_code=exc.error_code,
        )
        return

    # One atomic step (spec §11): report stored and job completed together,
    # and the insert stays first-write-wins against a racing duplicate.
    store.complete_job(job_id, report)
    log_event(
        component=COMPONENT,
        event="report_stored",
        job_id=job_id,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def run_worker(
    *,
    subscriber: Subscriber,
    candidates_topic: str,
    store: JobStore,
    ranker: CandidateRanker,
    exa: ExaClient,
) -> None:
    """Poll the candidates source forever and settle each message.

    `candidates_topic` is the InMemoryBroker topic locally and the Pub/Sub
    subscription name in production. Ack only after handle_batch returns
    (spec §14); on failure, nack for redelivery and keep polling so one bad
    batch no longer kills the worker.

    UNCERTAIN: a later duplicate of a ranking_failed job has no report, so
    this worker will rank again rather than treating failed as terminal.
    """
    while True:
        envelope = subscriber.pull(candidates_topic, CandidateBatchMessage)
        if envelope is None:
            time.sleep(_IDLE_SLEEP_SECONDS)
            continue
        try:
            handle_batch(envelope.message, store=store, ranker=ranker, exa=exa)
        except Exception:
            # Transient store failure: redelivery retries it. The duplicate
            # check keeps a retried batch from ranking twice.
            # UNCERTAIN: a persistent failure redelivers until the Pub/Sub
            # retention/dead-letter policy gives up; no in-process retry cap.
            log_event(
                component=COMPONENT,
                event="handle_failed",
                job_id=envelope.message.plan.job_id,
            )
            envelope.nack()
            continue
        envelope.ack()
