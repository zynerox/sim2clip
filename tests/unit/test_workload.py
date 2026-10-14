from sim2clip.simulation import SimulationConfig, generate_workload
from sim2clip.simulation.workload import workload_hash


def test_workload_is_independent_of_cashier_count():
    first = generate_workload(SimulationConfig(cashiers=1, customers=100, seed=42))
    second = generate_workload(SimulationConfig(cashiers=3, customers=100, seed=42))
    assert first == second
    assert workload_hash(first) == workload_hash(second)


def test_different_seeds_generate_different_workloads():
    first = generate_workload(SimulationConfig(cashiers=1, customers=20, seed=42))
    second = generate_workload(SimulationConfig(cashiers=1, customers=20, seed=43))
    assert workload_hash(first) != workload_hash(second)


def test_workload_shape_and_arrival_order():
    samples = generate_workload(SimulationConfig(cashiers=3, customers=100))
    assert len(samples) == 100
    assert [sample.customer_id for sample in samples] == list(range(100))
    assert samples[0].arrival_time == 0.0
    assert all(
        left.arrival_time <= right.arrival_time
        for left, right in zip(samples, samples[1:], strict=False)
    )
    assert all(sample.service_time > 0.0 for sample in samples)


def test_arrival_and_service_random_streams_are_independent():
    config = SimulationConfig(cashiers=1, customers=20)
    original = generate_workload(config)
    changed_service = generate_workload(config.model_copy(update={"mean_service_time": 50.0}))
    changed_arrival = generate_workload(config.model_copy(update={"mean_interarrival_time": 20.0}))
    assert [sample.arrival_time for sample in original] == [s.arrival_time for s in changed_service]
    assert [sample.service_time for sample in original] == [s.service_time for s in changed_arrival]


def test_hash_is_canonical_for_input_order(fixed_samples):
    assert workload_hash(fixed_samples) == workload_hash(fixed_samples[::-1])


def test_tiny_positive_mean_does_not_create_an_infinite_resampling_loop():
    config = SimulationConfig(cashiers=1, customers=1, mean_service_time=5e-324)
    samples = generate_workload(config)
    assert samples[0].service_time > 0.0
