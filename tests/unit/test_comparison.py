import pytest
from pydantic import ValidationError

from sim2clip.simulation import ComparisonRequest, SimulationConfig, run_comparison


def test_comparison_reuses_the_exact_workload_for_each_seed():
    configs = (
        SimulationConfig(cashiers=1, customers=100),
        SimulationConfig(cashiers=3, customers=100),
    )
    result = run_comparison(configs, seeds=(42, 43))
    assert len(result.results) == 4
    for seed in result.seeds:
        runs = [run for run in result.results if run.config.seed == seed]
        assert len(runs) == 2
        assert runs[0].customer_samples == runs[1].customer_samples
        assert runs[0].workload_hash == runs[1].workload_hash
        assert runs[0].metrics.average_service_time == pytest.approx(
            runs[1].metrics.average_service_time
        )
    first_seed = [run for run in result.results if run.config.seed == 42][0]
    second_seed = [run for run in result.results if run.config.seed == 43][0]
    assert first_seed.workload_hash != second_seed.workload_hash


def test_comparison_aggregates_real_results_and_signed_differences():
    result = run_comparison(
        (SimulationConfig(cashiers=1), SimulationConfig(cashiers=3)), seeds=(42, 43)
    )
    for summary in result.summaries:
        runs = [run for run in result.results if run.config.cashiers == summary.cashiers]
        values = [run.metrics.average_wait_time for run in runs]
        assert summary.average_wait_time.mean == pytest.approx(sum(values) / len(values))
        assert summary.average_wait_time.minimum == min(values)
        assert summary.average_wait_time.maximum == max(values)
    by_id = {run.result_id: run for run in result.results}
    for delta in result.paired_differences:
        baseline = by_id[delta.baseline_result_id].metrics
        candidate = by_id[delta.candidate_result_id].metrics
        assert delta.average_wait_time_difference == pytest.approx(
            candidate.average_wait_time - baseline.average_wait_time
        )
        assert (
            delta.max_queue_length_difference
            == candidate.max_queue_length - baseline.max_queue_length
        )


def test_zero_baseline_has_no_invented_percentage_and_ties_are_preserved():
    configs = (SimulationConfig(cashiers=1, customers=1), SimulationConfig(cashiers=3, customers=1))
    result = run_comparison(configs, seeds=(42,))
    assert result.winner_cashiers == (1, 3)
    assert result.paired_differences[0].wait_time_reduction_percent is None
    assert result.paired_differences[0].average_wait_time_difference == 0.0


def test_comparison_is_fully_reproducible():
    configs = (
        SimulationConfig(cashiers=1, customers=20),
        SimulationConfig(cashiers=3, customers=20),
    )
    assert (
        run_comparison(configs, seeds=(42, 43)).model_dump_json()
        == run_comparison(configs, seeds=(42, 43)).model_dump_json()
    )


@pytest.mark.parametrize("field", ["customers", "mean_service_time", "mean_interarrival_time"])
def test_comparison_rejects_confounded_experiments(field):
    baseline = SimulationConfig(cashiers=1)
    values = baseline.model_dump()
    values.update({"cashiers": 3, field: getattr(baseline, field) + 1})
    with pytest.raises(ValidationError, match="same workload"):
        run_comparison((baseline, SimulationConfig.model_validate(values)), seeds=(42,))


@pytest.mark.parametrize("seeds", [(), (42, 42), (-1,), (True,), tuple(range(11))])
def test_comparison_rejects_invalid_or_duplicate_seeds(seeds):
    with pytest.raises(ValidationError):
        ComparisonRequest(
            experiments=(SimulationConfig(cashiers=1), SimulationConfig(cashiers=3)), seeds=seeds
        )


def test_comparison_requires_two_distinct_cashier_counts():
    with pytest.raises(ValidationError):
        run_comparison((SimulationConfig(cashiers=1),), seeds=(42,))
    with pytest.raises(ValidationError):
        run_comparison((SimulationConfig(cashiers=1), SimulationConfig(cashiers=1)), seeds=(42,))


def test_omitted_seeds_use_a_common_config_seed():
    result = run_comparison(
        (SimulationConfig(cashiers=1, seed=7), SimulationConfig(cashiers=3, seed=7))
    )
    assert result.seeds == (7,)
    with pytest.raises(ValueError, match="seed"):
        run_comparison((SimulationConfig(cashiers=1, seed=7), SimulationConfig(cashiers=3, seed=8)))
