"""Component B worker: consume search plans, run the loop, publish candidates.

Implements the spec §9 sequence around the loop: skip jobs that already
published, run the search, publish the batch, then record a best-effort Redis
completion key. The in-memory broker has no real ack (it pops on receive);
the GCP adapter in spec §11 must withhold acknowledgement until publish
succeeds.
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
    """Poll the search-plans topic forever and handle each message.

    INCOMPLETE: InMemoryBroker.receive pops immediately, so a crash after
    receive but before publish drops the plan. Real Pub/Sub ack/nack is §11.
    """
    while True:
        plan = subscriber.receive(plans_topic, SearchPlanMessage)
        if plan is None:
            time.sleep(_IDLE_SLEEP_SECONDS)
            continue
        # FOLLOW-UP: a poison message or publish failure currently crashes the
        # process. The GCP adapter should nack and keep polling.
        handle_plan(
            plan,
            cache=cache,
            exa=exa,
            decider=decider,
            publisher=publisher,
            candidates_topic=candidates_topic,
        )
