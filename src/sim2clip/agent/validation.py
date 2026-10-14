"""Independent evidence checks, not another simulator or engine metric call."""

import json
from collections import deque
from hashlib import sha256
from itertools import groupby
from math import isclose
from statistics import fmean

from sim2clip.agent.state import JSONDict
from sim2clip.simulation.schemas import (
    ComparisonRequest,
    ComparisonResult,
    SimulationConfig,
    SimulationResult,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def close(actual: float, expected: float, label: str) -> None:
    require(isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), f"Incorrect {label}")


def contract_digest(value: object) -> str:
    """Verify the published v1 canonical digest without importing engine code."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_request(name: str, arguments: JSONDict) -> tuple[JSONDict, int]:
    if name == "run_simulation":
        config = SimulationConfig.model_validate(arguments)
        return config.model_dump(mode="json"), config.customers
    if name == "compare_simulations":
        request = ComparisonRequest.model_validate(arguments)
        cost = request.experiments[0].customers * len(request.experiments) * len(request.seeds)
        return request.model_dump(mode="json"), cost
    raise ValueError(f"Tool is not allowed: {name}")


def experiment_design(calls: list[JSONDict]) -> JSONDict:
    configs: list[SimulationConfig] = []
    seeds: set[int] = set()
    for call in calls:
        if call["name"] == "run_simulation":
            config = SimulationConfig.model_validate(call["arguments"])
            configs.append(config)
            seeds.add(config.seed)
        else:
            request = ComparisonRequest.model_validate(call["arguments"])
            configs.extend(request.experiments)
            seeds.update(request.seeds)
    first = configs[0].model_dump(mode="json", exclude={"cashiers", "seed"})
    require(
        all(
            config.model_dump(mode="json", exclude={"cashiers", "seed"}) == first
            for config in configs
        ),
        "Every call must use the same demand settings",
    )
    return {
        "cashiers": list(dict.fromkeys(config.cashiers for config in configs)),
        "workload": first,
        "seeds": sorted(seeds),
    }


def validate_simulation(result: SimulationResult) -> None:
    ordered_samples = sorted(
        result.customer_samples, key=lambda sample: (sample.arrival_time, sample.customer_id)
    )
    require(
        result.workload_hash
        == contract_digest([s.model_dump(mode="json") for s in ordered_samples]),
        "Workload hash does not match customer samples",
    )
    require(
        result.result_id
        == contract_digest(
            {
                "config": result.config.model_dump(mode="json"),
                "workload_hash": result.workload_hash,
                "workload_source": result.workload_source,
                "metadata": result.metadata.model_dump(mode="json"),
            }
        ),
        "Result ID does not match provenance",
    )
    samples = {sample.customer_id: sample for sample in result.customer_samples}
    records = {record.customer_id: record for record in result.customer_records}
    # Rebuild occupancy and lifecycles from events, without trusting returned
    # metrics or invoking the engine that produced them.
    queue: deque[int] = deque()
    active: dict[int, int] = {}
    lifecycle: dict[int, str] = {}
    arrivals: list[int] = []
    maximum_queue = 0
    for _, batch in groupby(result.events, key=lambda event: event.timestamp):
        for event in batch:
            customer = event.customer_id
            sample, record = samples[customer], records[customer]
            close(record.arrival_time, sample.arrival_time, "record arrival time")
            close(record.service_time, sample.service_time, "record service duration")
            if event.event_type == "customer_arrived":
                require(customer not in lifecycle, "Duplicate arrival")
                close(event.timestamp, sample.arrival_time, "arrival event time")
                lifecycle[customer] = "waiting"
                arrivals.append(customer)
                queue.append(customer)
            elif event.event_type == "customer_started":
                require(lifecycle.get(customer) == "waiting", "Service must follow arrival")
                require(bool(queue) and queue[0] == customer, "FIFO service order violated")
                require(event.cashier_id not in active, "Cashier capacity exceeded")
                require(event.cashier_id == record.cashier_id, "Start cashier differs from record")
                close(event.timestamp, record.service_start_time, "start event time")
                queue.popleft()
                assert event.cashier_id is not None
                active[event.cashier_id] = customer
                lifecycle[customer] = "serving"
            else:
                require(lifecycle.get(customer) == "serving", "Finish must follow service")
                require(active.get(event.cashier_id or 0) == customer, "Wrong cashier at finish")
                close(event.timestamp, record.service_end_time, "finish event time")
                assert event.cashier_id is not None
                del active[event.cashier_id]
                lifecycle[customer] = "finished"
            require(event.queue_length_after_event == len(queue), "Event queue length is incorrect")
            require(len(active) <= result.config.cashiers, "Resource capacity exceeded")
        # Same-time arrivals and starts can produce transient queue states.
        # Check work conservation and peak length after the whole batch settles.
        require(
            not queue or len(active) == result.config.cashiers, "Idle cashier with waiting queue"
        )
        maximum_queue = max(maximum_queue, len(queue))
    require(not queue and not active, "Simulation did not drain")
    require(all(value == "finished" for value in lifecycle.values()), "Unfinished customers")
    expected_arrivals = [
        sample.customer_id
        for sample in sorted(
            result.customer_samples, key=lambda sample: (sample.arrival_time, sample.customer_id)
        )
    ]
    require(arrivals == expected_arrivals, "Arrival order differs from workload")
    # Cross-check published metrics against verified records and event state.
    metrics = result.metrics
    close(
        metrics.average_wait_time,
        fmean(r.service_start_time - r.arrival_time for r in records.values()),
        "average wait",
    )
    close(
        metrics.average_service_time,
        fmean(r.service_end_time - r.service_start_time for r in records.values()),
        "average service",
    )
    close(metrics.max_wait_time, max(r.wait_time for r in records.values()), "maximum wait")
    close(
        metrics.simulation_end_time, max(r.service_end_time for r in records.values()), "end time"
    )
    require(metrics.max_queue_length == maximum_queue, "Incorrect settled maximum queue length")


def validate_comparison(result: ComparisonResult) -> None:
    request = result.request
    require(
        result.comparison_id
        == contract_digest(
            {
                "request": request.model_dump(mode="json"),
                "result_ids": [run.result_id for run in result.results],
            }
        ),
        "Comparison ID does not match provenance",
    )
    runs = {(run.config.cashiers, run.config.seed): run for run in result.results}
    for config in request.experiments:
        for seed in request.seeds:
            run = runs[config.cashiers, seed]
            require(
                run.config == config.model_copy(update={"seed": seed}),
                "Run changed workload configuration",
            )
            validate_simulation(run)
    means: dict[int, float] = {}
    for summary in result.summaries:
        group = [runs[summary.cashiers, seed] for seed in request.seeds]
        require(
            summary.result_ids == tuple(run.result_id for run in group),
            "Summary result references differ",
        )
        for metric in ("average_wait_time", "max_queue_length", "simulation_end_time"):
            values = [float(getattr(run.metrics, metric)) for run in group]
            reported = getattr(summary, metric)
            close(reported.mean, fmean(values), f"{metric} mean")
            close(reported.minimum, min(values), f"{metric} minimum")
            close(reported.maximum, max(values), f"{metric} maximum")
        means[summary.cashiers] = summary.average_wait_time.mean
    seen: set[tuple[int, int]] = set()
    for difference in result.paired_differences:
        key = (difference.candidate_cashiers, difference.seed)
        require(key not in seen, "Duplicate paired difference")
        seen.add(key)
        require(
            difference.candidate_cashiers != result.baseline_cashiers,
            "Baseline cannot be candidate",
        )
        require(key in runs, "Unknown paired difference")
        baseline = runs[result.baseline_cashiers, difference.seed]
        candidate = runs[key]
        require(
            difference.baseline_result_id == baseline.result_id
            and difference.candidate_result_id == candidate.result_id,
            "Difference references do not match runs",
        )
        delta = candidate.metrics.average_wait_time - baseline.metrics.average_wait_time
        close(difference.average_wait_time_difference, delta, "paired wait delta")
        require(
            difference.max_queue_length_difference
            == candidate.metrics.max_queue_length - baseline.metrics.max_queue_length,
            "Incorrect paired queue delta",
        )
        close(
            difference.simulation_end_time_difference,
            candidate.metrics.simulation_end_time - baseline.metrics.simulation_end_time,
            "paired end delta",
        )
        if baseline.metrics.average_wait_time == 0:
            require(
                difference.wait_time_reduction_percent is None, "Zero baseline needs null reduction"
            )
        else:
            require(difference.wait_time_reduction_percent is not None, "Missing wait reduction")
            assert difference.wait_time_reduction_percent is not None
            close(
                difference.wait_time_reduction_percent,
                -delta / baseline.metrics.average_wait_time * 100,
                "paired reduction",
            )
    ranked = tuple(sorted(means, key=lambda cashiers: (means[cashiers], cashiers)))
    require(result.ranking_cashiers == ranked, "Incorrect ranking")
    winners = tuple(
        cashiers
        for cashiers in ranked
        if isclose(means[cashiers], means[ranked[0]], rel_tol=1e-9, abs_tol=1e-9)
    )
    require(result.winner_cashiers == winners, "Incorrect winners or ties")


def validate_response(
    name: str, arguments: JSONDict, result: SimulationResult | ComparisonResult
) -> None:
    if name == "run_simulation":
        require(isinstance(result, SimulationResult), "Wrong response type")
        assert isinstance(result, SimulationResult)
        require(
            result.config == SimulationConfig.model_validate(arguments),
            "Response config differs from requested config",
        )
        validate_simulation(result)
    else:
        require(isinstance(result, ComparisonResult), "Wrong response type")
        assert isinstance(result, ComparisonResult)
        require(
            result.request == ComparisonRequest.model_validate(arguments),
            "Response request differs from submitted comparison",
        )
        validate_comparison(result)
