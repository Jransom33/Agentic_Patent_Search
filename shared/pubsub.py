"""GCP Pub/Sub adapter behind the shared Publisher / Subscriber interfaces.

Spec §11: explicit ack and nack. publish() blocks until GCP confirms the
message so callers never treat an unsent message as published, and pull()
returns an Envelope whose ack/nack settle the exact message that was pulled.
Tests keep using InMemoryBroker; this module is only wired in production.
"""

from google.api_core import exceptions as gcp_exceptions
from google.cloud import pubsub_v1
from pydantic import BaseModel

from shared.logging import log_event
from shared.messaging import Envelope, T, decode, encode

# ASSUMPTION: 30s is generous for a <=256KB publish and lets a pull long-poll
# without the worker busy-looping. Not tuned against real GCP latency yet.
_PUBLISH_TIMEOUT_SECONDS = 30.0
_PULL_TIMEOUT_SECONDS = 30.0


class GcpPubSub:
    """Publisher and Subscriber implementation for real GCP Pub/Sub."""

    def __init__(self, project_id: str) -> None:
        """Create the GCP clients once; both are reused for every call.

        Credentials come from the VM's attached service account (Application
        Default Credentials), so no key files are handled here.
        """
        self._project = project_id
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()

    def publish(self, topic: str, model: BaseModel) -> None:
        """Encode the model and publish it, waiting for GCP's confirmation.

        result() raises on timeout or rejection, which callers rely on: an
        input message is only acked after this returns (spec §14).
        """
        path = self._publisher.topic_path(self._project, topic)
        self._publisher.publish(path, encode(model)).result(_PUBLISH_TIMEOUT_SECONDS)

    def pull(self, source: str, model_type: type[T]) -> Envelope[T] | None:
        """Synchronously pull one message from the named subscription.

        Returns None when the wait times out with nothing available, matching
        the worker loops' poll-and-sleep pattern. Poison payloads are acked
        and dropped so Pub/Sub does not redeliver them forever.
        """
        # One message at a time matches the intentionally single-job workers.
        path = self._subscriber.subscription_path(self._project, source)
        try:
            response = self._subscriber.pull(
                subscription=path, max_messages=1, timeout=_PULL_TIMEOUT_SECONDS
            )
        except gcp_exceptions.DeadlineExceeded:
            return None
        if not response.received_messages:
            return None
        received = response.received_messages[0]

        def ack() -> None:
            self._subscriber.acknowledge(subscription=path, ack_ids=[received.ack_id])

        def nack() -> None:
            # Deadline 0 tells Pub/Sub to redeliver immediately.
            self._subscriber.modify_ack_deadline(
                subscription=path, ack_ids=[received.ack_id], ack_deadline_seconds=0
            )

        try:
            message = decode(received.message.data, model_type)
        except Exception:
            log_event(component="messaging", event="poison_message_dropped")
            ack()
            return None
        # UNCERTAIN: a long Component B search can exceed the subscription's
        # ack deadline, causing a duplicate delivery mid-run. Provisioning
        # must set the maximum 600s deadline; the workers' duplicate checks
        # absorb anything beyond that. No lease extension in this version.
        return Envelope(message=message, ack=ack, nack=nack)
