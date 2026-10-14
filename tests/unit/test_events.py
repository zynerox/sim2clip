import pytest
from pydantic import ValidationError

from sim2clip.simulation.events import EventRecorder


def test_recorder_snapshots_are_immutable_and_have_all_replay_fields():
    recorder = EventRecorder(cashiers=1)
    arrived = recorder.record("customer_arrived", timestamp=0.0, customer_id=0)
    before = recorder.events
    started = recorder.record("customer_started", timestamp=0.0, customer_id=0, cashier_id=1)
    finished = recorder.record("customer_finished", timestamp=2.0, customer_id=0, cashier_id=1)
    assert [e.queue_length_after_event for e in recorder.events] == [1, 0, 0]
    assert [e.sequence for e in recorder.events] == [0, 1, 2]
    assert before == (arrived,)
    assert set(started.model_dump()) == {
        "sequence",
        "timestamp",
        "event_type",
        "customer_id",
        "cashier_id",
        "queue_length_after_event",
    }
    assert finished.cashier_id == 1
    with pytest.raises(ValidationError):
        started.timestamp = 8.0


def test_illegal_transition_does_not_corrupt_recorder():
    recorder = EventRecorder(cashiers=1)
    with pytest.raises(ValueError):
        recorder.record("customer_started", timestamp=0.0, customer_id=0, cashier_id=1)
    recorder.record("customer_arrived", timestamp=0.0, customer_id=0)
    before = recorder.events
    with pytest.raises(ValueError):
        recorder.record("customer_started", timestamp=-1.0, customer_id=0, cashier_id=1)
    assert recorder.events == before
    assert (
        recorder.record("customer_started", timestamp=0.0, customer_id=0, cashier_id=1).sequence
        == 1
    )


def test_recorder_rejects_fifo_violation_busy_cashier_and_wrong_completion():
    recorder = EventRecorder(cashiers=1)
    recorder.record("customer_arrived", timestamp=0.0, customer_id=0)
    recorder.record("customer_arrived", timestamp=0.0, customer_id=1)
    with pytest.raises(ValueError, match="FIFO"):
        recorder.record("customer_started", timestamp=0.0, customer_id=1, cashier_id=1)
    recorder.record("customer_started", timestamp=0.0, customer_id=0, cashier_id=1)
    with pytest.raises(ValueError, match="busy"):
        recorder.record("customer_started", timestamp=0.0, customer_id=1, cashier_id=1)
    with pytest.raises(ValueError):
        recorder.record("customer_finished", timestamp=1.0, customer_id=1, cashier_id=1)


def test_recorder_rejects_duplicate_arrival_and_backwards_clock():
    recorder = EventRecorder(cashiers=1)
    recorder.record("customer_arrived", timestamp=1.0, customer_id=0)
    with pytest.raises(ValueError):
        recorder.record("customer_arrived", timestamp=1.0, customer_id=0)
    with pytest.raises(ValueError):
        recorder.record("customer_arrived", timestamp=0.0, customer_id=1)
