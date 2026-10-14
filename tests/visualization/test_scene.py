"""Actual event states, deterministic coordinates, aggregation and tied transitions."""

from dataclasses import asdict

import pytest

from sim2clip.simulation import SimulationConfig, run_simulation
from sim2clip.simulation.schemas import ComparisonResult, CustomerSample, SimulationResult
from sim2clip.visualization.data import VisualizationError, select_demo_runs
from sim2clip.visualization.replay import EventReplay
from sim2clip.visualization.scene import ScenePose, SceneTimeline, cashier_position, queue_position


def assert_conservation(pose: ScenePose, run: SimulationResult) -> None:
    snapshot = EventReplay(run).seek(pose.timestamp)
    ids = [p.customer_id for p in pose.entities]
    assert len(ids) == len(set(ids))
    assert all(snapshot.customer_states[p.customer_id] == p.status for p in pose.entities)
    assert pose.queue_length == len(snapshot.queue) == pose.queue_visible + pose.queue_hidden
    assert pose.completed == snapshot.completed == pose.completed_visible + pose.completed_hidden
    busy = sum(c is not None for c in snapshot.cashiers.values())
    assert busy == pose.serving_visible + pose.serving_hidden
    assert pose.not_arrived + pose.queue_length + busy + pose.completed == run.config.customers
    assert len(pose.cashiers) == run.config.cashiers
    for cashier in pose.cashiers:
        assert cashier.customer_id == snapshot.cashiers[cashier.cashier_id]
    for person in pose.entities:
        if person.status == "waiting":
            assert snapshot.queue[person.queue_rank] == person.customer_id
        elif person.status == "serving":
            assert snapshot.cashiers[person.cashier_id] == person.customer_id
    for index, person in enumerate(pose.entities):
        assert all(
            abs(person.x - other.x) >= 28 - 1e-8 or abs(person.y - other.y) >= 48 - 1e-8
            for other in pose.entities[index + 1 :]
        )


def test_scene_matches_every_event_and_between_events(official: ComparisonResult) -> None:
    runs = select_demo_runs(official)
    horizon = max(r.metrics.simulation_end_time for r in runs)
    for run in runs:
        scene = SceneTimeline(run, end_time=horizon)
        times = sorted({e.timestamp for e in run.events})
        for index, timestamp in enumerate(times):
            assert_conservation(scene.seek(timestamp), run)
            if index:
                assert_conservation(scene.seek((timestamp + times[index - 1]) / 2), run)


def test_seek_positions_are_independent_of_history(official: ComparisonResult) -> None:
    run = select_demo_runs(official)[0]
    scene = SceneTimeline(run)
    for timestamp in (850, 0, 17.25, 1800, 700.125, 2419, 1, 850):
        actual = scene.seek(timestamp)
        fresh = SceneTimeline(run).seek(timestamp)
        assert actual == fresh
        before = asdict(actual)
        scene.seek(run.metrics.simulation_end_time)
        assert asdict(actual) == before  # later seeks cannot mutate returned poses


def test_fifo_targets_and_cashier_coordinates(official: ComparisonResult) -> None:
    for run in select_demo_runs(official):
        scene = SceneTimeline(run)
        for timestamp in (0, 100, 500, 850, run.metrics.simulation_end_time):
            pose = scene.seek(timestamp)
            for entity in pose.entities:
                if entity.status == "waiting":
                    assert (entity.target_x, entity.target_y) == queue_position(
                        entity.queue_rank, scene.design
                    )
                elif entity.status == "serving":
                    assert (entity.target_x, entity.target_y) == cashier_position(
                        entity.cashier_id, run.config.cashiers, scene.design
                    )
            positions = {(p.x, p.y) for p in pose.cashiers}
            assert len(positions) == run.config.cashiers


def test_motion_is_continuous_but_state_changes_at_exact_event(
    hand_result: SimulationResult,
) -> None:
    scene = SceneTimeline(hand_result)
    before, exact, after = scene.seek(4 - 1e-7), scene.seek(4), scene.seek(4 + 1e-7)
    customer_before = next(p for p in before.entities if p.customer_id == 1)
    customer_exact = next(p for p in exact.entities if p.customer_id == 1)
    customer_after = next(p for p in after.entities if p.customer_id == 1)
    assert customer_before.status == "waiting"
    assert customer_exact.status == customer_after.status == "serving"
    assert customer_exact.x == pytest.approx(customer_before.x, abs=1e-5)
    assert customer_exact.y == pytest.approx(customer_before.y, abs=1e-5)
    assert exact.completed == 1 and exact.cashiers[0].customer_id == 1
    assert scene.seek(4.4).entities != exact.entities


@pytest.mark.parametrize("cashiers", [1, 3, 8, 100])
def test_large_population_and_hidden_cashiers_have_exact_counts(cashiers: int) -> None:
    run = run_simulation(
        SimulationConfig(cashiers=cashiers, customers=120),
        tuple(CustomerSample(customer_id=i, arrival_time=0, service_time=10) for i in range(120)),
    )
    scene = SceneTimeline(run)
    for timestamp in (0, 5, 10, 10.5, 25, run.metrics.simulation_end_time):
        pose = scene.seek(timestamp)
        assert_conservation(pose, run)
        assert pose.queue_visible <= 24
        assert pose.serving_visible <= 6
        assert pose.completed_visible <= 3
        assert len({(p.x, p.y) for p in pose.cashiers}) == cashiers
    assert scene.seek(0).queue_hidden >= max(0, 120 - cashiers - 24)


def test_equal_time_arrivals_starts_and_finishes_settle_once() -> None:
    run = run_simulation(
        SimulationConfig(cashiers=3, customers=6),
        tuple(CustomerSample(customer_id=i, arrival_time=0, service_time=1) for i in range(6)),
    )
    scene = SceneTimeline(run)
    start = scene.seek(0)
    assert start.queue_length == 3 and start.serving_visible + start.serving_hidden == 3
    assert all(p.status != "waiting" for p in start.entities if p.customer_id < 3)
    middle = scene.seek(1)
    assert middle.queue_length == 0 and middle.completed == 3
    assert {c.customer_id for c in middle.cashiers} == {3, 4, 5}
    assert_conservation(middle, run)
    assert scene.seek(2).entities == ()
    assert scene.seek(0) == start


def test_shared_horizon_and_customer_identity(official: ComparisonResult) -> None:
    a, b = select_demo_runs(official)
    scenes = [SceneTimeline(r, end_time=a.metrics.simulation_end_time) for r in (a, b)]
    poses = [s.seek(850) for s in scenes]
    assert poses[0].timestamp == poses[1].timestamp == 850
    assert poses[0].queue_length == 60 and poses[1].completed == 91
    for scene in scenes:
        for t in (0, 100, 850):
            assert all(p.variant == p.customer_id % 4 for p in scene.seek(t).entities)
    done = scenes[1].seek(b.metrics.simulation_end_time + 25)
    assert done.completed == 100 and done.entities == ()


def test_noncontiguous_customer_ids_are_preserved() -> None:
    run = run_simulation(
        SimulationConfig(cashiers=1, customers=3),
        tuple(CustomerSample(customer_id=i, arrival_time=0, service_time=3) for i in (9, 20, 71)),
    )
    pose = SceneTimeline(run).seek(1)
    assert {p.customer_id for p in pose.entities} == {9, 20, 71}
    assert pose.cashiers[0].customer_id == 9


@pytest.mark.parametrize("timestamp", [-1, float("nan"), float("inf")])
def test_bad_scene_time_is_rejected(hand_result: SimulationResult, timestamp: float) -> None:
    with pytest.raises(VisualizationError):
        SceneTimeline(hand_result).seek(timestamp)


def test_scene_horizon_cannot_shorten_experiment(hand_result: SimulationResult) -> None:
    with pytest.raises(VisualizationError):
        SceneTimeline(hand_result, end_time=6)
