"""Paired server experiments; change one declared axis and retain every run."""

from collections.abc import Mapping, Sequence
from math import isclose
from statistics import fmean
from typing import Literal

from sim2clip.scenarios.server_contracts import (
    RequestSample,
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerComparisonRun,
    ServerExperimentSummary,
    ServerMetricSummary,
    ServerPairedDifference,
    ServerSimulationResult,
)

from .server_model import _simulate
from .server_workload import generate_server_workload, normalize_server_samples, stable_server_hash


def run_server_comparison(
    request: ServerComparisonRequest,
    samples_by_seed: Mapping[int, Sequence[RequestSample]] | None = None,
) -> ServerComparisonResult:
    request = ServerComparisonRequest.model_validate(request)
    if samples_by_seed is not None and (
        any(type(seed) is not int for seed in samples_by_seed)
        or set(samples_by_seed) != set(request.seeds)
    ):
        raise ValueError("Supplied samples must exactly cover the requested integer seeds")
    source: Literal["generated", "supplied"] = (
        "generated" if samples_by_seed is None else "supplied"
    )
    results: list[ServerComparisonRun] = []
    differences: list[ServerPairedDifference] = []
    for seed in request.seeds:
        config = request.experiments[0].config.model_copy(update={"seed": seed})
        samples = (
            generate_server_workload(config)
            if samples_by_seed is None
            else normalize_server_samples(config, samples_by_seed[seed])
        )
        runs: list[ServerSimulationResult] = []
        for experiment in request.experiments:
            run = _simulate(experiment.config.model_copy(update={"seed": seed}), samples, source)
            runs.append(run)
            results.append(ServerComparisonRun(group_id=experiment.group_id, result=run))
        baseline = runs[0]
        for experiment, candidate in zip(request.experiments[1:], runs[1:], strict=True):
            a, b = baseline.metrics, candidate.metrics
            differences.append(
                ServerPairedDifference(
                    seed=seed,
                    candidate_group_id=experiment.group_id,
                    baseline_result_id=baseline.result_id,
                    candidate_result_id=candidate.result_id,
                    average_response_time_difference=b.average_response_time
                    - a.average_response_time,
                    p95_response_time_difference=b.p95_response_time - a.p95_response_time,
                    average_wait_time_difference=b.average_wait_time - a.average_wait_time,
                    max_queue_length_difference=b.max_queue_length - a.max_queue_length,
                    simulation_end_time_difference=b.simulation_end_time - a.simulation_end_time,
                )
            )

    def summarize(values: Sequence[float]) -> ServerMetricSummary:
        return ServerMetricSummary(mean=fmean(values), minimum=min(values), maximum=max(values))

    summaries: list[ServerExperimentSummary] = []
    for experiment in request.experiments:
        group = [r.result for r in results if r.group_id == experiment.group_id]
        summaries.append(
            ServerExperimentSummary(
                group_id=experiment.group_id,
                result_ids=tuple(r.result_id for r in group),
                average_response_time=summarize([r.metrics.average_response_time for r in group]),
                p95_response_time=summarize([r.metrics.p95_response_time for r in group]),
                average_wait_time=summarize([r.metrics.average_wait_time for r in group]),
                max_queue_length=summarize([float(r.metrics.max_queue_length) for r in group]),
                simulation_end_time=summarize([r.metrics.simulation_end_time for r in group]),
            )
        )
    ranked = sorted(summaries, key=lambda s: s.average_response_time.mean)
    best = ranked[0].average_response_time.mean
    winners = tuple(
        s.group_id
        for s in ranked
        if isclose(s.average_response_time.mean, best, rel_tol=1e-9, abs_tol=1e-9)
    )
    return ServerComparisonResult(
        comparison_id=stable_server_hash(
            {
                "contract": "server-lb-comparison-v1",
                "request": request.model_dump(mode="json"),
                "runs": [
                    {
                        "group_id": r.group_id,
                        "seed": r.result.config.seed,
                        "result_id": r.result.result_id,
                    }
                    for r in results
                ],
            }
        ),
        request=request,
        baseline_group_id=request.experiments[0].group_id,
        seeds=request.seeds,
        results=tuple(results),
        summaries=tuple(summaries),
        paired_differences=tuple(differences),
        ranking_group_ids=tuple(s.group_id for s in ranked),
        winner_group_ids=winners,
    )
