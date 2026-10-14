"""Normative M1 fixtures: expected values do not come from engine helpers."""

import pytest

from sim2clip.scenarios.server_contracts import RequestSample, ServerSimulationConfig
from sim2clip.simulation.server_model import run_server_simulation


def samples(rows):
    return tuple(RequestSample(request_id=i, arrival_time=a, work=w) for i, a, w in rows)


STRATEGY_SAMPLES = ((0, 0, 10), (1, 1, 1), (2, 3, 1))


@pytest.mark.parametrize(
    "strategy,servers,rows,records,metrics,event_rows",
    [
        (
            "round_robin",
            1,
            ((0, 0, 3), (1, 1, 2), (2, 4, 1)),
            [(1, 0, 3, 0, 3), (1, 3, 5, 2, 4), (1, 5, 6, 1, 2)],
            (3, 4, 1, 1, 6),
            [
                (0, "arrived", 0, None, 0),
                (0, "routed", 0, 1, 1),
                (0, "started", 0, 1, 0),
                (1, "arrived", 1, None, 0),
                (1, "routed", 1, 1, 1),
                (3, "completed", 0, 1, 1),
                (3, "started", 1, 1, 0),
                (4, "arrived", 2, None, 0),
                (4, "routed", 2, 1, 1),
                (5, "completed", 1, 1, 1),
                (5, "started", 2, 1, 0),
                (6, "completed", 2, 1, 0),
            ],
        ),
        (
            "round_robin",
            2,
            STRATEGY_SAMPLES,
            [(1, 0, 10, 0, 10), (2, 1, 2, 0, 1), (1, 10, 11, 7, 8)],
            (19 / 3, 10, 7 / 3, 1, 11),
            [
                (0, "arrived", 0, None, 0),
                (0, "routed", 0, 1, 1),
                (0, "started", 0, 1, 0),
                (1, "arrived", 1, None, 0),
                (1, "routed", 1, 2, 1),
                (1, "started", 1, 2, 0),
                (2, "completed", 1, 2, 0),
                (3, "arrived", 2, None, 0),
                (3, "routed", 2, 1, 1),
                (10, "completed", 0, 1, 1),
                (10, "started", 2, 1, 0),
                (11, "completed", 2, 1, 0),
            ],
        ),
        (
            "least_outstanding",
            2,
            STRATEGY_SAMPLES,
            [(1, 0, 10, 0, 10), (2, 1, 2, 0, 1), (2, 3, 4, 0, 1)],
            (4, 10, 0, 0, 10),
            [
                (0, "arrived", 0, None, 0),
                (0, "routed", 0, 1, 1),
                (0, "started", 0, 1, 0),
                (1, "arrived", 1, None, 0),
                (1, "routed", 1, 2, 1),
                (1, "started", 1, 2, 0),
                (2, "completed", 1, 2, 0),
                (3, "arrived", 2, None, 0),
                (3, "routed", 2, 2, 1),
                (3, "started", 2, 2, 0),
                (4, "completed", 2, 2, 0),
                (10, "completed", 0, 1, 0),
            ],
        ),
    ],
)
def test_m1_hand_calculated_records_metrics_and_full_event_sequence(
    strategy, servers, rows, records, metrics, event_rows
):
    config = ServerSimulationConfig(
        requests=3,
        routing_strategy=strategy,
        servers=tuple({"server_id": i + 1} for i in range(servers)),
    )
    result = run_server_simulation(config, samples(rows))
    assert [
        (r.server_id, r.processing_start_time, r.processing_end_time, r.wait_time, r.response_time)
        for r in result.request_records
    ] == records
    m = result.metrics
    assert (
        m.average_response_time,
        m.p95_response_time,
        m.average_wait_time,
        m.max_queue_length,
        m.simulation_end_time,
    ) == pytest.approx(metrics)
    assert m.completed_requests == 3
    assert [
        (
            e.timestamp,
            e.event_type.removeprefix("request_"),
            e.request_id,
            e.server_id,
            e.total_queue_length_after_event,
        )
        for e in result.events
    ] == event_rows


def test_heterogeneous_servers_convert_work_to_duration():
    config = ServerSimulationConfig(
        requests=2,
        servers=(
            {"server_id": 1, "processing_rate": 1},
            {"server_id": 2, "processing_rate": 2},
        ),
    )
    result = run_server_simulation(config, samples(((0, 0, 2), (1, 0, 2))))
    assert [r.processing_time for r in result.request_records] == [2, 1]
    assert result.metrics.average_response_time == 1.5
    assert result.metrics.p95_response_time == 2
    assert result.metrics.max_queue_length == 0


def test_completion_then_old_waiter_then_new_arrival():
    result = run_server_simulation(
        ServerSimulationConfig(requests=3), samples(((0, 0, 1), (1, 0.5, 1), (2, 1, 1)))
    )
    assert [(e.event_type, e.request_id) for e in result.events if e.timestamp == 1] == [
        ("request_completed", 0),
        ("request_started", 1),
        ("request_arrived", 2),
        ("request_routed", 2),
    ]


def test_simultaneous_noncontinuous_ids_and_completion_order():
    config = ServerSimulationConfig(
        requests=4,
        routing_strategy="least_outstanding",
        servers=({"server_id": 3}, {"server_id": 8}),
    )
    result = run_server_simulation(config, samples(((9, 0, 1), (2, 0, 1), (20, 1, 1), (15, 1, 1))))
    assert [
        (e.request_id, e.server_id) for e in result.events if e.event_type == "request_routed"
    ] == [(2, 3), (9, 8), (15, 3), (20, 8)]
    assert [(e.event_type, e.request_id) for e in result.events if e.timestamp == 1] == [
        ("request_completed", 2),
        ("request_completed", 9),
        ("request_arrived", 15),
        ("request_routed", 15),
        ("request_started", 15),
        ("request_arrived", 20),
        ("request_routed", 20),
        ("request_started", 20),
    ]


@pytest.mark.parametrize("strategy", ["round_robin", "least_outstanding"])
def test_capacity_fifo_and_population_conservation_in_every_event(strategy):
    from collections import deque

    result = run_server_simulation(
        ServerSimulationConfig(
            requests=100,
            routing_strategy=strategy,
            arrivals={"mode": "batch"},
            servers=({"server_id": 1}, {"server_id": 2, "processing_rate": 2}),
        )
    )
    queues = {1: deque(), 2: deque()}
    active, dispatching, finished, arrived = {}, set(), set(), set()
    for event in result.events:
        rid, sid = event.request_id, event.server_id
        if event.event_type == "request_arrived":
            assert rid not in arrived
            arrived.add(rid)
            dispatching.add(rid)
        elif event.event_type == "request_routed":
            dispatching.remove(rid)
            queues[sid].append(rid)
        elif event.event_type == "request_started":
            assert sid not in active
            assert queues[sid].popleft() == rid
            active[sid] = rid
        else:
            assert active.pop(sid) == rid
            finished.add(rid)
        assert event.total_queue_length_after_event == sum(map(len, queues.values()))
        if sid is not None:
            assert event.server_queue_length_after_event == len(queues[sid])
            assert event.server_active_after_event == int(sid in active)
        assert len(arrived) == len(dispatching) + sum(map(len, queues.values())) + len(
            active
        ) + len(finished)
    assert len(finished) == 100 and not active and not dispatching


def test_unrepresentable_processing_time_and_invalid_copies_fail():
    config = ServerSimulationConfig(requests=1, servers=({"server_id": 1, "processing_rate": 1e6},))
    with pytest.raises(ValueError, match="represent"):
        run_server_simulation(config, samples(((0, 1e9, 1e-20),)))
    with pytest.raises(ValueError):
        run_server_simulation(config.model_copy(update={"requests": True}))
    with pytest.raises(ValueError):
        run_server_simulation(config, (samples(((0, 0, 1),))[0].model_copy(update={"work": -1}),))
    with pytest.raises(ValueError, match="count"):
        run_server_simulation(ServerSimulationConfig(requests=2), samples(((0, 0, 1),)))
    with pytest.raises(ValueError, match="unique"):
        run_server_simulation(ServerSimulationConfig(requests=2), samples(((0, 0, 1), (0, 1, 1))))


def test_fixed_seed_entire_json_and_rr_cursor_reset():
    config = ServerSimulationConfig(requests=13, servers=({"server_id": 3}, {"server_id": 7}))
    first = run_server_simulation(config)
    run_server_simulation(config.model_copy(update={"seed": 43}))
    second = run_server_simulation(config)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.request_records[0].server_id == 3


def test_unused_server_has_zero_metrics_and_initial_transient_is_not_queue_peak():
    result = run_server_simulation(
        ServerSimulationConfig(requests=1, servers=({"server_id": 1}, {"server_id": 5}))
    )
    assert result.events[1].total_queue_length_after_event == 1
    assert result.metrics.max_queue_length == 0
    assert result.metrics.per_server[1].completed_requests == 0
    assert result.metrics.per_server[1].max_queue_length == 0


def test_global_queue_peak_is_not_sum_of_node_peaks():
    result = run_server_simulation(
        ServerSimulationConfig(requests=4, servers=({"server_id": 1}, {"server_id": 2})),
        samples(((0, 0, 2), (1, 0, 10), (2, 1, 1), (3, 4, 1))),
    )
    assert [m.max_queue_length for m in result.metrics.per_server] == [1, 1]
    assert result.metrics.max_queue_length == 1


def test_tiny_processing_duration_underflows_explicitly():
    with pytest.raises(ValueError, match="represent"):
        run_server_simulation(
            ServerSimulationConfig(requests=1, servers=({"server_id": 1, "processing_rate": 1e6},)),
            samples(((0, 0, 5e-324),)),
        )


def test_maximum_request_bound_drains_with_exact_four_events_per_request():
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=10000,
            arrivals={"mode": "batch"},
            workload={"mode": "constant", "work": 1},
            servers=tuple({"server_id": i + 1} for i in range(32)),
        )
    )
    assert len(result.events) == 40000
    assert result.metrics.completed_requests == 10000
    assert result.metrics.simulation_end_time == 313


@pytest.mark.parametrize("rate,mean,seed", [(0.001, 0.001, 6), (0.001, 0.025, 15), (0.1, 0.001, 2)])
def test_absolute_clock_does_not_lose_one_ulp_when_advancing(rate, mean, seed):
    from sim2clip.agent.server_validation import validate_server_simulation

    result = run_server_simulation(
        ServerSimulationConfig(
            requests=30,
            seed=seed,
            servers=(
                {"server_id": 1, "processing_rate": rate},
                {"server_id": 3, "processing_rate": 2 * rate},
            ),
            workload={"mode": "exponential", "mean_work": mean},
        )
    )
    validate_server_simulation(result)
    for record in result.request_records:
        assert record.processing_end_time == record.processing_start_time + record.processing_time


def test_p95_nearest_rank_with_21_requests_is_twentieth_order_statistic():
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=21, arrivals={"mode": "batch"}, workload={"mode": "constant", "work": 1}
        )
    )
    assert result.metrics.average_response_time == 11
    assert result.metrics.p95_response_time == 20
    assert result.metrics.simulation_end_time == 21


def test_least_outstanding_counts_waiting_requests_in_addition_to_active():
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=4,
            routing_strategy="least_outstanding",
            servers=({"server_id": 1}, {"server_id": 2}),
        ),
        samples(((0, 0, 10), (1, 0, 10), (2, 1, 1), (3, 2, 1))),
    )
    assert [r.server_id for r in result.request_records] == [1, 2, 1, 2]


def test_all_completions_then_all_old_waiters_before_arrival_routing():
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=5,
            routing_strategy="least_outstanding",
            servers=({"server_id": 1}, {"server_id": 2}),
        ),
        samples(((0, 0, 1), (1, 0, 1), (2, 0.5, 1), (3, 0.5, 1), (4, 1, 1))),
    )
    assert [(e.event_type, e.request_id) for e in result.events if e.timestamp == 1] == [
        ("request_completed", 0),
        ("request_completed", 1),
        ("request_started", 2),
        ("request_started", 3),
        ("request_arrived", 4),
        ("request_routed", 4),
    ]
