"""Pub/Sub codec and in-memory FIFO broker."""

import pytest
from pydantic import BaseModel

from shared.bounds import MAX_PUBSUB_PAYLOAD_BYTES
from shared.messaging import InMemoryBroker, PayloadTooLargeError, decode, encode
from shared.models import CandidateBatchMessage, SearchPlanMessage
from tests.conftest import candidate_batch, search_plan


def test_encode_decode_round_trips_both_message_types():
    plan = search_plan()
    batch = candidate_batch()
    assert decode(encode(plan), SearchPlanMessage) == plan
    assert decode(encode(batch), CandidateBatchMessage) == batch


def test_oversized_payload_rejected_on_encode_and_decode():
    class Blob(BaseModel):
        data: str

    with pytest.raises(PayloadTooLargeError):
        encode(Blob(data="x" * MAX_PUBSUB_PAYLOAD_BYTES))
    with pytest.raises(PayloadTooLargeError):
        decode(b"x" * (MAX_PUBSUB_PAYLOAD_BYTES + 1), SearchPlanMessage)


def test_broker_is_fifo_per_topic_and_empty_returns_none():
    broker = InMemoryBroker()
    first = search_plan(job_id="job-a")
    second = search_plan(job_id="job-b")
    broker.publish("plans", first)
    broker.publish("plans", second)
    assert broker.receive("plans", SearchPlanMessage).job_id == "job-a"
    assert broker.receive("plans", SearchPlanMessage).job_id == "job-b"
    assert broker.receive("plans", SearchPlanMessage) is None
    assert broker.receive("other", SearchPlanMessage) is None
