import random
from unittest.mock import Mock

import pytest

from sim2clip.scenarios.server_contracts import ServerSimulationConfig
from sim2clip.simulation import server_workload as workload


def test_published_seed_42_vector_and_canonical_hash():
    config = ServerSimulationConfig(requests=4)
    samples = workload.generate_server_workload(config)
    assert [(s.arrival_time, s.work) for s in samples] == [
        (0.0, 0.08175610728494545),
        (0.0007022752086315954, 0.021439609079440848),
        (0.021157706188541733, 0.00783690344287325),
        (0.02412599545081316, 0.004644916160983055),
    ]
    assert workload.server_workload_hash(samples) == (
        "882b6542f94d197afd74e31f39976b3741167c0bcb8b2a47f3e9fa1694d00f47"
    )


def test_streams_are_independent_of_routing_resources_and_other_distribution():
    base = ServerSimulationConfig(requests=10)
    samples = workload.generate_server_workload(base)
    changed = base.model_copy(update={"routing_strategy": "least_outstanding"})
    assert workload.generate_server_workload(changed) == samples
    changed = ServerSimulationConfig(
        requests=10,
        servers=(
            {"server_id": 3, "processing_rate": 0.1},
            {"server_id": 9, "processing_rate": 100},
        ),
    )
    assert workload.generate_server_workload(changed) == samples
    batch = ServerSimulationConfig(requests=10, arrivals={"mode": "batch", "arrival_time": 7})
    constant = ServerSimulationConfig(requests=10, workload={"mode": "constant", "work": 2})
    assert [s.work for s in workload.generate_server_workload(batch)] == [s.work for s in samples]
    assert [s.arrival_time for s in workload.generate_server_workload(constant)] == [
        s.arrival_time for s in samples
    ]
    assert {s.arrival_time for s in workload.generate_server_workload(batch)} == {7}
    assert {s.work for s in workload.generate_server_workload(constant)} == {2}


def test_different_seeds_and_global_random_state():
    state = random.getstate()
    first = workload.generate_server_workload(ServerSimulationConfig(requests=10))
    second = workload.generate_server_workload(ServerSimulationConfig(requests=10, seed=43))
    assert first != second
    assert state == random.getstate()
    assert workload.server_workload_hash(first) == workload.server_workload_hash(first[::-1])
    assert workload.server_workload_hash(first) != workload.server_workload_hash(second)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1.0, 1e9])
def test_invalid_draw_is_never_clipped_or_resampled(monkeypatch, value):
    rng = Mock()
    rng.expovariate.return_value = value
    monkeypatch.setattr(workload, "_stream", lambda *_: rng)
    with pytest.raises(ValueError):
        workload.generate_server_workload(ServerSimulationConfig(requests=1))
    assert rng.expovariate.call_count == 1


def test_zero_draw_retry_is_bounded_and_can_recover(monkeypatch):
    rng = Mock()
    rng.expovariate.return_value = 0
    monkeypatch.setattr(workload, "_stream", lambda *_: rng)
    with pytest.raises(ValueError, match="128"):
        workload.generate_server_workload(ServerSimulationConfig(requests=1))
    assert rng.expovariate.call_count == 128
    rng.expovariate.side_effect = [0, 1]
    assert workload.generate_server_workload(ServerSimulationConfig(requests=1))[0].work == 0.025


def test_nonadvancing_arrival_is_an_error(monkeypatch):
    rng = Mock()
    rng.expovariate.side_effect = [1e11, 1e-20]
    monkeypatch.setattr(workload, "_stream", lambda *_: rng)
    config = ServerSimulationConfig(requests=3, workload={"mode": "constant", "work": 1})
    with pytest.raises(ValueError, match="advancing"):
        workload.generate_server_workload(config)


def test_model_copy_cannot_bypass_generator_contracts():
    bad = ServerSimulationConfig().model_copy(update={"seed": True})
    with pytest.raises(ValueError):
        workload.generate_server_workload(bad)
