"""Pub/Sub payload codec, ack-aware messaging interfaces, and a fake broker.

Components depend on Publisher / Subscriber so tests can inject
InMemoryBroker and production can inject shared.pubsub.GcpPubSub. Spec §14:
a message must not be acked until the component's output is published or
stored, so pull() hands back an Envelope with explicit ack() / nack().
"""

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from shared.bounds import MAX_PUBSUB_PAYLOAD_BYTES
from shared.logging import log_event

T = TypeVar("T", bound=BaseModel)


class PayloadTooLargeError(ValueError):
    pass


def encode(model: BaseModel) -> bytes:
    """Turn a search-plan or candidate-batch model into JSON bytes for Pub/Sub.

    Rejects the message if it is larger than MAX_PUBSUB_PAYLOAD_BYTES.
    """
    # ASSUMPTION: UTF-8 JSON is the only wire format we will use with Pub/Sub.
    payload = model.model_dump_json().encode("utf-8")
    if len(payload) > MAX_PUBSUB_PAYLOAD_BYTES:
        raise PayloadTooLargeError(
            f"payload is {len(payload)} bytes; max is {MAX_PUBSUB_PAYLOAD_BYTES}"
        )
    return payload


def decode(payload: bytes, model_type: type[T]) -> T:
    """Parse JSON bytes back into the given pydantic model type.

    Also rejects oversized payloads so a huge message never reaches validation.
    """
    # UNCERTAIN: caller must pass the right model_type; a search-plan payload
    # decoded as CandidateBatchMessage will just raise ValidationError.
    if len(payload) > MAX_PUBSUB_PAYLOAD_BYTES:
        raise PayloadTooLargeError(
            f"payload is {len(payload)} bytes; max is {MAX_PUBSUB_PAYLOAD_BYTES}"
        )
    return model_type.model_validate_json(payload)


class Publisher(Protocol):
    def publish(self, topic: str, model: BaseModel) -> None:
        """Put one encoded message on a topic."""
        ...


@dataclass(frozen=True)
class Envelope(Generic[T]):
    """One pulled message plus the controls to settle it.

    Callers ack() only after their output is published/stored, or nack() to
    request redelivery. Exactly one of the two should be called once.
    """

    message: T
    ack: Callable[[], None]
    nack: Callable[[], None]


class Subscriber(Protocol):
    def pull(self, source: str, model_type: type[T]) -> Envelope[T] | None:
        """Take the next message from a source, or None if nothing is waiting.

        `source` is a topic name for InMemoryBroker and a subscription name
        for the GCP adapter. An undecodable (poison) payload is logged,
        acked so it never redelivers, and reported as None.
        """
        ...


class InMemoryBroker:
    """FIFO queues keyed by topic. No GCP calls."""

    def __init__(self) -> None:
        """Start with no topics. Queues are created when something is published."""
        self._topics: dict[str, deque[bytes]] = defaultdict(deque)

    def publish(self, topic: str, model: BaseModel) -> None:
        """Encode the model and append it to that topic's queue."""
        self._topics[topic].append(encode(model))

    def pull(self, source: str, model_type: type[T]) -> Envelope[T] | None:
        """Take the oldest message on the topic as an Envelope, or None if empty.

        The payload is removed on pull; ack() is then a no-op and nack() puts
        it back at the front so the next pull retries it, mirroring Pub/Sub
        redelivery closely enough for tests.
        """
        # ASSUMPTION: single-threaded use. A crash between pull and ack still
        # loses the message in this fake (the process dies with the queue),
        # which only real Pub/Sub can fix.
        queue = self._topics[source]
        if not queue:
            return None
        payload = queue.popleft()
        try:
            message = decode(payload, model_type)
        except Exception:
            # Poison message: it will never decode, so drop instead of requeue.
            log_event(component="messaging", event="poison_message_dropped")
            return None
        return Envelope(
            message=message,
            ack=lambda: None,
            nack=lambda: queue.appendleft(payload),
        )

    def receive(self, topic: str, model_type: type[T]) -> T | None:
        """Test convenience: pull and immediately ack in one step.

        Lets tests drain a topic to assert what was published without
        handling Envelopes. Workers must use pull() so acks stay explicit.
        """
        envelope = self.pull(topic, model_type)
        if envelope is None:
            return None
        envelope.ack()
        return envelope.message
