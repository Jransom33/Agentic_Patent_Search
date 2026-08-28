"""Component B worker: consume search plans, run the loop, publish candidates.

Implements the spec §9 sequence around the loop: skip jobs that already
published, run the search, publish the batch, then record a best-effort Redis
completion key. The worker acks its input only after handle_plan succeeds,
so a crash or failure before publish leads to redelivery, not a lost plan.
"""

import time

from search.cache import SearchCache
from search.loop import run_search_loop
from shared.logging import log_event
from shared.messaging import Publisher, Subscriber
from shared.models import SearchPlanMessage
from shared.providers.claude import SearchDecider
from shared.providers.exa import ExaClient

COMPONENT = "search"
# How long to wait when the local fake broker has nothing queued.
# ASSUMPTION: 1s is fine for a class-demo poll loop; GCP Pub/Sub will block.
_IDLE_SLEEP_SECONDS = 1.0


def handle_plan(
    plan: SearchPlanMessage,
    *,
    cache: SearchCache,
    exa: ExaClient,
    decider: SearchDecider,
    publisher: Publisher,
    candidates_topic: str,
) -> None:
    """Process one SearchPlanMessage through to a published candidate batch.

    Duplicate jobs (Redis completion key already set) return without searching.
    Does not ack the input; the caller / GCP adapter does that after this
    returns. Publish failures raise so the input is not treated as done.
    """
    started = time.monotonic()
    job_id = plan.job_id

    # Duplicate Pub/Sub delivery: the batch was already published for this job.
    # UNCERTAIN: if Redis is down this raises and the worker skips the job until
    # a retry. Treating a Redis error as "not done" would risk a double publish.
    if cache.job_is_done(job_id):
        log_event(component=COMPONENT, event="duplicate_acked", job_id=job_id)
        return

    log_event(component=COMPONENT, event="search_started", job_id=job_id)
    # The loop never raises: success or a sanitized search_failed / decision_failed.
    batch = run_search_loop(plan, cache=cache, exa=exa, decider=decider)
    publisher.publish(candidates_topic, batch)

    # Best-effort only: a Redis blip must not undo a successful publish.
    # ASSUMPTION: terminal-failure batches still set the done key so a duplicate
    # delivery does not re-run Exa/Claude; Component C marks the job failed.
    try:
        cache.mark_job_done(job_id)
    except Exception:
        log_event(component=COMPONENT, event="done_key_failed", job_id=job_id)

    log_event(
        component=COMPONENT,
        event="batch_published",
        job_id=job_id,
        duration_ms=(time.monotonic() - started) * 1000,
        error_code=batch.error_code,
    )


def run_worker(
    *,
    subscriber: Subscriber,
    publisher: Publisher,
    plans_topic: str,
    candidates_topic: str,
    cache: SearchCache,
    exa: ExaClient,
    decider: SearchDecider,
) -> None:
    """Poll the search-plans source forever and settle each message.

    `plans_topic` is the InMemoryBroker topic locally and the Pub/Sub
    subscription name in production. Ack only after handle_plan returns
    (spec §14); on failure, nack for redelivery and keep polling so one bad
    job no longer kills the worker.
    """
    while True:
        envelope = subscriber.pull(plans_topic, SearchPlanMessage)
        if envelope is None:
            time.sleep(_IDLE_SLEEP_SECONDS)
            continue
        try:
            handle_plan(
                envelope.message,
                cache=cache,
                exa=exa,
                decider=decider,
                publisher=publisher,
                candidates_topic=candidates_topic,
            )
        except Exception:
            # Transient failure (publish or Redis duplicate check). Redelivery
            # retries it; the loop's hard ceilings bound any repeated cost.
            # UNCERTAIN: a persistent failure redelivers until the Pub/Sub
            # retention/dead-letter policy gives up; no in-process retry cap.
            log_event(
                component=COMPONENT,
                event="handle_failed",
                job_id=envelope.message.job_id,
            )
            envelope.nack()
            continue
        envelope.ack()
