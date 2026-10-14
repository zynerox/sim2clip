"""Hand-calculated states, tie settling, backward seeks and damaged evidence."""

from dataclasses import asdict

import pytest

from sim2clip.simulation import SimulationConfig, run_simulation
from sim2clip.simulation.schemas import ComparisonResult, CustomerSample, SimulationResult
from sim2clip.visualization.data import VisualizationError, parse_result, select_demo_runs
from sim2clip.visualization.replay import EventReplay


def test_hand_calculated_event_states_and_ties(hand_result: SimulationResult) -> None:
    replay = EventReplay(hand_result)
    first = replay.seek(0)
    assert first.queue == () and first.cashiers == {1: 0} and first.events_applied == 2
    assert first.customer_states == {0: "serving", 1: "not_arrived", 2: "not_arrived"}
    waiting = replay.seek(1)
    assert waiting.queue == (1, 2) and waiting.completed == 0
    serving = replay.seek(4)
    assert serving.queue == (2,) and serving.cashiers == {1: 1} and serving.completed == 1
    assert replay.seek(6).cashiers == {1: 2}
    assert replay.seek(7).completed == 3 and replay.seek(7).queue == ()
    assert replay.seek(100).cashiers == {1: None}
    assert replay.seek(0) == first
    assert waiting.queue == (1, 2)  # snapshots cannot change under later replay


def test_same_timestamp_immediate_service_has_no_phantom_queue() -> None:
    result = run_simulation(
        SimulationConfig(cashiers=2, customers=2),
        (
            CustomerSample(customer_id=0, arrival_time=0, service_time=2),
            CustomerSample(customer_id=1, arrival_time=0, service_time=2),
        ),
    )
    state = EventReplay(result).seek(0)
    assert state.queue == () and state.cashiers == {1: 0, 2: 1} and state.events_applied == 4
    assert result.metrics.max_queue_length == 0


def test_shared_clock_keeps_completed_group_finished(official: ComparisonResult) -> None:
    first, second = select_demo_runs(official)
    clock = second.metrics.simulation_end_time
    a, b = EventReplay(first).seek(clock), EventReplay(second).seek(clock)
    assert a.timestamp == b.timestamp
    assert b.completed == 100 and a.completed < 100 and b.queue == ()
    assert b == EventReplay(second).seek(clock)


@pytest.mark.parametrize("timestamp", [-1, float("nan"), float("inf")])
def test_invalid_replay_time(hand_result: SimulationResult, timestamp: float) -> None:
    with pytest.raises(VisualizationError, match="finite"):
        EventReplay(hand_result).seek(timestamp)


@pytest.mark.parametrize("damage", ["missing_events", "missing_field", "out_of_order"])
def test_missing_or_disordered_events_rejected(hand_result: SimulationResult, damage: str) -> None:
    value = hand_result.model_dump(mode="json")
    if damage == "missing_events":
        value["events"].pop()
    elif damage == "missing_field":
        del value["events"][0]["queue_length_after_event"]
    else:
        value["events"][0]["sequence"] = 2
    with pytest.raises(VisualizationError, match="complete MCP"):
        parse_result(value)


@pytest.mark.parametrize("damage", ["queue", "cashier", "timestamp", "metric"])
def test_semantic_corruption_rejected(hand_result: SimulationResult, damage: str) -> None:
    value = hand_result.model_dump(mode="json")
    if damage == "queue":
        value["events"][0]["queue_length_after_event"] = 0
    elif damage == "cashier":
        value["events"][5]["customer_id"] = 0
    elif damage == "timestamp":
        value["events"][2]["timestamp"] = 0.5
    else:
        value["metrics"]["average_wait_time"] = 100.0
    with pytest.raises(VisualizationError):
        EventReplay(SimulationResult.model_validate(value))


def test_varied_seeds_groups_and_single_result_loading(hand_result: SimulationResult) -> None:
    assert select_demo_runs(hand_result) == (hand_result,)
    assert asdict(EventReplay(hand_result).seek(7))["completed"] == 3
    from sim2clip.simulation import run_comparison

    other = run_comparison(
        (SimulationConfig(cashiers=2, customers=8), SimulationConfig(cashiers=4, customers=8)),
        seeds=(7, 9),
    )
    chosen = select_demo_runs(parse_result(other.model_dump(mode="json")))
    assert [r.config.cashiers for r in chosen] == [2, 4]
    assert all(r.config.seed == 7 for r in chosen)  # never generate a missing seed 42
