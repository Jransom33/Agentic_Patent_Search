"""Pub/Sub payload codec and a fake broker for tests.

GCP client wiring comes later. Components should depend on Publisher /
Subscriber so tests can inject InMemoryBroker.
"""

from collections import defaultdict, deque
from typing import Protocol, TypeVar

from pydantic import BaseModel

from agentic_patents.bounds import MAX_PUBSUB_PAYLOAD_BYTES

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


class Subscriber(Protocol):
    def receive(self, topic: str, model_type: type[T]) -> T | None:
        """Take the next message on a topic, or None if the topic is empty."""
        ...


class InMemoryBroker:
    """FIFO queues keyed by topic. No GCP calls."""

    def __init__(self) -> None:
        """Start with no topics. Queues are created when something is published."""
        self._topics: dict[str, deque[bytes]] = defaultdict(deque)

    def publish(self, topic: str, model: BaseModel) -> None:
        """Encode the model and append it to that topic's queue."""
        self._topics[topic].append(encode(model))

    def receive(self, topic: str, model_type: type[T]) -> T | None:
        """Pop the oldest message on the topic and validate it as model_type.

        Returns None when the topic has nothing waiting.
        """
        # INCOMPLETE: no ack/nack. Spec §11 says do not ack until output is
        # published/stored; the GCP adapter must do that. This fake pops
        # immediately, so a crash after receive drops the message.
        # FOLLOW-UP: does not replay duplicates (Pub/Sub is at-least-once).
        queue = self._topics[topic]
        if not queue:
            return None
        return decode(queue.popleft(), model_type)
