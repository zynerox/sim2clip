from collections import deque

import pytest

from sim2clip.simulation import CustomerSample, SimulationConfig, run_simulation


@pytest.mark.parametrize(
    ("cashiers", "waits", "average_wait", "peak_queue", "end_time"),
    [(1, [0.0, 3.0, 5.0], 8 / 3, 2, 6.0), (3, [0.0, 0.0, 0.0], 0.0, 0, 3.0)],
)
def test_hand_calculated_case(fixed_samples, cashiers, waits, average_wait, peak_queue, end_time):
    result = run_simulation(SimulationConfig(cashiers=cashiers, customers=3), fixed_samples)
    assert [record.wait_time for record in result.customer_records] == waits
    assert result.metrics.average_wait_time == pytest.approx(average_wait)
    assert result.metrics.average_service_time == 2.0
    assert result.metrics.max_queue_length == peak_queue
    assert result.metrics.completed_customers == 3
    assert result.metrics.simulation_end_time == end_time
    assert len(result.events) == 9


def test_fifo_follows_arrival_order_not_customer_id():
    samples = (
        CustomerSample(customer_id=1, arrival_time=2.0, service_time=1.0),
        CustomerSample(customer_id=8, arrival_time=0.0, service_time=10.0),
        CustomerSample(customer_id=3, arrival_time=1.0, service_time=1.0),
    )
    result = run_simulation(SimulationConfig(cashiers=1, customers=3), samples)
    starts = [
        event.customer_id for event in result.events if event.event_type == "customer_started"
    ]
    assert starts == [8, 3, 1]


def test_simultaneous_arrivals_use_customer_id_as_stable_tie_break(fixed_samples):
    result = run_simulation(SimulationConfig(cashiers=1, customers=3), fixed_samples[::-1])
    starts = [
        event.customer_id for event in result.events if event.event_type == "customer_started"
    ]
    assert starts == [0, 1, 2]


@pytest.mark.parametrize("cashiers", [1, 3, 10])
def test_every_event_replays_with_capacity_fifo_and_population_conservation(cashiers):
    result = run_simulation(SimulationConfig(cashiers=cashiers, customers=100))
    waiting = deque()
    serving = {}
    arrived = set()
    finished = set()
    last_timestamp = -1.0
    for sequence, event in enumerate(result.events):
        assert event.sequence == sequence
        assert event.timestamp >= last_timestamp
        last_timestamp = event.timestamp
        if event.event_type == "customer_arrived":
            assert event.customer_id not in arrived
            assert event.cashier_id is None
            arrived.add(event.customer_id)
            waiting.append(event.customer_id)
        elif event.event_type == "customer_started":
            assert waiting.popleft() == event.customer_id
            assert event.cashier_id not in serving
            assert 1 <= event.cashier_id <= cashiers
            serving[event.cashier_id] = event.customer_id
        else:
            assert serving.pop(event.cashier_id) == event.customer_id
            finished.add(event.customer_id)
        assert event.queue_length_after_event == len(waiting)
        assert len(serving) <= cashiers
        assert len(arrived) == len(waiting) + len(serving) + len(finished)
    assert not waiting and not serving
    assert len(finished) == 100


def test_same_seed_reproduces_entire_result_and_json():
    config = SimulationConfig(cashiers=3, customers=100, seed=42)
    first, second = run_simulation(config), run_simulation(config)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_completion_and_arrival_at_the_same_timestamp():
    samples = (
        CustomerSample(customer_id=0, arrival_time=0.0, service_time=2.0),
        CustomerSample(customer_id=1, arrival_time=2.0, service_time=1.0),
    )
    result = run_simulation(SimulationConfig(cashiers=1, customers=2), samples)
    assert [record.wait_time for record in result.customer_records] == [0.0, 0.0]
    assert result.metrics.max_queue_length == 0


def test_cashier_ids_can_be_reused_after_completion():
    samples = tuple(
        CustomerSample(customer_id=index, arrival_time=0.0, service_time=1.0) for index in range(8)
    )
    result = run_simulation(SimulationConfig(cashiers=2, customers=8), samples)
    assert [record.service_start_time for record in result.customer_records] == [
        0.0,
        0.0,
        1.0,
        1.0,
        2.0,
        2.0,
        3.0,
        3.0,
    ]
    assert {record.cashier_id for record in result.customer_records} == {1, 2}


def test_supplied_samples_must_match_customer_count(fixed_samples):
    with pytest.raises(ValueError, match="count"):
        run_simulation(SimulationConfig(cashiers=1, customers=2), fixed_samples)


def test_duplicate_customer_ids_are_rejected(fixed_samples):
    with pytest.raises(ValueError, match="unique"):
        run_simulation(SimulationConfig(cashiers=1, customers=3), (fixed_samples[0],) * 3)


def test_unrepresentable_service_completion_fails_explicitly():
    sample = CustomerSample(customer_id=0, arrival_time=1e300, service_time=1.0)
    with pytest.raises(ValueError, match="represent"):
        run_simulation(SimulationConfig(cashiers=1, customers=1), (sample,))


def test_unvalidated_model_copy_cannot_bypass_engine_parameter_checks():
    config = SimulationConfig(cashiers=1).model_copy(update={"customers": -100})
    with pytest.raises(ValueError):
        run_simulation(config)


def test_unvalidated_sample_copy_cannot_bypass_engine_checks(fixed_samples):
    invalid = fixed_samples[0].model_copy(update={"service_time": -2.0})
    with pytest.raises(ValueError):
        run_simulation(SimulationConfig(cashiers=1, customers=1), (invalid,))
