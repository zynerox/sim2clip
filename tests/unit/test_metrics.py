import pytest

from sim2clip.simulation import SimulationConfig, run_simulation
from sim2clip.simulation.metrics import calculate_metrics, settled_queue_lengths


def test_metrics_can_be_recalculated_from_saved_records_and_events(fixed_samples):
    result = run_simulation(SimulationConfig(cashiers=1, customers=3), fixed_samples)
    assert calculate_metrics(result.customer_records, result.events) == result.metrics
    assert list(settled_queue_lengths(result.events)) == [(0.0, 2), (3.0, 1), (5.0, 0), (6.0, 0)]


def test_zero_duration_queue_transitions_do_not_create_a_peak(fixed_samples):
    result = run_simulation(SimulationConfig(cashiers=3, customers=3), fixed_samples)
    assert max(event.queue_length_after_event for event in result.events) > 0
    assert result.metrics.max_queue_length == 0


def test_empty_metrics_input_is_rejected():
    with pytest.raises(ValueError):
        calculate_metrics((), ())
