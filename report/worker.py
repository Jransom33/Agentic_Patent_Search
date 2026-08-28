"""Component C worker: consume candidate batches, rank, and store reports.

Implements the spec §10 sequence around the pipeline: skip jobs that already
have a report, mark Component B terminal failures, otherwise rank and persist.
The in-memory broker has no real ack (it pops on receive); the GCP adapter in
spec §11 must withhold acknowledgement until the report is stored or the job
is marked failed.
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
    # FOLLOW-UP: a crash after save_report but before set_status(completed)
    # leaves the job in ranking; this skip will not heal that status.
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

    # save_report is first-write-wins, so a racing duplicate cannot replace it.
    store.save_report(job_id, report)
    store.set_status(job_id, JobStatus.COMPLETED)
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
    """Poll the candidates topic forever and handle each message.

    INCOMPLETE: InMemoryBroker.receive pops immediately, so a crash after
    receive but before save_report drops the batch. Real Pub/Sub ack/nack
    is §11.
    """
    while True:
        batch = subscriber.receive(candidates_topic, CandidateBatchMessage)
        if batch is None:
            time.sleep(_IDLE_SLEEP_SECONDS)
            continue
        # FOLLOW-UP: a poison message or store failure currently crashes the
        # process. The GCP adapter should nack and keep polling.
        # UNCERTAIN: a later duplicate of a ranking_failed job has no report,
        # so this worker will rank again rather than treating failed as terminal.
        handle_batch(batch, store=store, ranker=ranker, exa=exa)
