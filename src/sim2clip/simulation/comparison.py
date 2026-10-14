"""Paired comparison runner; results are observations, not significance claims."""

from collections.abc import Sequence
from math import isclose
from statistics import fmean

from .queue_model import run_simulation
from .schemas import (
    ComparisonRequest,
    ComparisonResult,
    ExperimentSummary,
    MetricSummary,
    PairedDifference,
    SimulationConfig,
    SimulationResult,
)
from .workload import generate_workload, stable_hash


def _summarize(values: Sequence[float]) -> MetricSummary:
    return MetricSummary(mean=fmean(values), minimum=min(values), maximum=max(values))


def run_comparison(
    experiments: Sequence[SimulationConfig], *, seeds: Sequence[int] | None = None
) -> ComparisonResult:
    """Change cashier count only. Generate ONE common workload per seed.

    With omitted seeds, run once using the experiments' common config seed.
    Explicit seeds override each experiment's seed. The official demo supplies
    seeds 42..46; the MCP request schema supplies the same default.
    The first experiment is the baseline. Deltas are candidate minus baseline.
    """
    configs = tuple(experiments)
    if seeds is None:
        if not configs:
            raise ValueError("At least two experiments are required")
        if len({config.seed for config in configs}) != 1:
            raise ValueError("Experiments must have a common seed, or pass explicit paired seeds")
        seeds = (configs[0].seed,)
    request = ComparisonRequest(experiments=configs, seeds=tuple(seeds))
    configs = request.experiments
    results: list[SimulationResult] = []
    differences: list[PairedDifference] = []
    for seed in request.seeds:
        baseline_config = configs[0].model_copy(update={"seed": seed})
        # Draw once per seed and share samples across all cashier counts.
        # This keeps arrival and service draws identical in the paired experiment.
        samples = generate_workload(baseline_config)
        runs = [
            run_simulation(config.model_copy(update={"seed": seed}), samples) for config in configs
        ]
        results.extend(runs)
        baseline = runs[0]
        for candidate in runs[1:]:
            wait_delta = candidate.metrics.average_wait_time - baseline.metrics.average_wait_time
            baseline_wait = baseline.metrics.average_wait_time
            differences.append(
                PairedDifference(
                    seed=seed,
                    baseline_result_id=baseline.result_id,
                    candidate_result_id=candidate.result_id,
                    candidate_cashiers=candidate.config.cashiers,
                    average_wait_time_difference=wait_delta,
                    max_queue_length_difference=(
                        candidate.metrics.max_queue_length - baseline.metrics.max_queue_length
                    ),
                    simulation_end_time_difference=(
                        candidate.metrics.simulation_end_time - baseline.metrics.simulation_end_time
                    ),
                    wait_time_reduction_percent=(
                        -wait_delta / baseline_wait * 100.0 if baseline_wait > 0.0 else None
                    ),
                )
            )
    summaries: list[ExperimentSummary] = []
    for config in configs:
        group = [run for run in results if run.config.cashiers == config.cashiers]
        summaries.append(
            ExperimentSummary(
                cashiers=config.cashiers,
                result_ids=tuple(run.result_id for run in group),
                average_wait_time=_summarize([run.metrics.average_wait_time for run in group]),
                max_queue_length=_summarize([float(run.metrics.max_queue_length) for run in group]),
                simulation_end_time=_summarize([run.metrics.simulation_end_time for run in group]),
            )
        )
    ranked = sorted(
        summaries, key=lambda summary: (summary.average_wait_time.mean, summary.cashiers)
    )
    best_wait = ranked[0].average_wait_time.mean
    winners = tuple(
        summary.cashiers
        for summary in ranked
        if isclose(summary.average_wait_time.mean, best_wait, rel_tol=1e-9, abs_tol=1e-9)
    )
    comparison_id = stable_hash(
        {
            "request": request.model_dump(mode="json"),
            "result_ids": [run.result_id for run in results],
        }
    )
    return ComparisonResult(
        comparison_id=comparison_id,
        request=request,
        baseline_cashiers=configs[0].cashiers,
        seeds=request.seeds,
        results=tuple(results),
        summaries=tuple(summaries),
        paired_differences=tuple(differences),
        ranking_cashiers=tuple(s.cashiers for s in ranked),
        winner_cashiers=winners,
    )
