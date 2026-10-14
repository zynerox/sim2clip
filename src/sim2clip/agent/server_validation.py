"""Evidence verification from server contracts and stdlib only; no engine imports.

Replay the supplied event stream, verify its mandatory successors and recompute
observations. This is deliberately separate from the SimPy scheduling code.
"""

import hashlib
import json
import math
import random
from collections import deque
from itertools import groupby

from sim2clip.scenarios.server_contracts import (
    BatchArrivals,
    ConstantWork,
    RequestSample,
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerSimulationConfig,
    ServerSimulationEvent,
    ServerSimulationResult,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(actual: float, expected: float, label: str) -> None:
    _require(
        math.isfinite(actual)
        and math.isfinite(expected)
        and abs(actual - expected) <= max(1e-9, 4 * max(math.ulp(actual), math.ulp(expected))),
        f"Incorrect {label}: {actual!r}, expected {expected!r}",
    )


def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verify_generated_samples(result: ServerSimulationResult) -> None:
    config = result.config
    streams = {
        name: random.Random(
            int.from_bytes(
                hashlib.sha256(
                    f"sim2clip-server-workload-v1:{config.seed}:{name}".encode("ascii")
                ).digest(),
                "big",
            )
        )
        for name in ("arrival", "work")
    }

    def draw(name: str, mean: float) -> float:
        for _ in range(128):
            value = streams[name].expovariate(1.0) * mean
            _require(math.isfinite(value) and value >= 0, "Invalid generated draw")
            if value > 0:
                return value
        raise ValueError("Generated draw exhausted zero resamples")

    arrival = config.arrivals.arrival_time if isinstance(config.arrivals, BatchArrivals) else 0.0
    for i, sample in enumerate(result.request_samples):
        if i and not isinstance(config.arrivals, BatchArrivals):
            previous = arrival
            arrival += draw("arrival", config.arrivals.mean_interarrival_time)
            _require(arrival > previous, "Unrepresentable generated arrival")
        work = (
            config.workload.work
            if isinstance(config.workload, ConstantWork)
            else draw("work", config.workload.mean_work)
        )
        _require(
            sample == RequestSample(request_id=i, arrival_time=arrival, work=work),
            "Generated samples do not match declared seed and version",
        )


def validate_server_simulation(
    result: ServerSimulationResult, expected_config: ServerSimulationConfig | None = None
) -> None:
    result = ServerSimulationResult.model_validate(result)
    config = result.config
    if expected_config is not None:
        _require(
            config == ServerSimulationConfig.model_validate(expected_config),
            "Unexpected effective simulation config",
        )
    samples = result.request_samples
    sample_hash = _digest(
        {
            "workload_version": "server-workload-1",
            "samples": [s.model_dump(mode="json") for s in samples],
        }
    )
    _require(result.workload_hash == sample_hash, "Incorrect workload hash")
    _require(
        result.result_id
        == _digest(
            {
                "contract": "server-lb-result-v1",
                "config": config.model_dump(mode="json"),
                "workload_hash": sample_hash,
                "workload_source": result.workload_source,
                "metadata": result.metadata.model_dump(mode="json"),
            }
        ),
        "Incorrect result ID",
    )
    if result.workload_source == "generated":
        _verify_generated_samples(result)

    ids = tuple(s.server_id for s in config.servers)
    rates = {s.server_id: s.processing_rate for s in config.servers}
    records = {r.request_id: r for r in result.request_records}
    demand = {s.request_id: s for s in samples}
    queues: dict[int, deque[int]] = {sid: deque() for sid in ids}
    active: dict[int, int] = {}
    phases = dict.fromkeys(demand, 0)
    population = [config.requests, 0, 0, 0, 0]
    peaks = dict.fromkeys(ids, 0)
    completed = dict.fromkeys(ids, 0)
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    global_peak, arrival_index, cursor = 0, 0, 0

    for timestamp, iterator in groupby(result.events, key=lambda e: e.timestamp):
        batch = tuple(iterator)
        offset = 0
        future = [records[rid].processing_end_time for rid in active.values()]
        if arrival_index < len(samples):
            future.append(samples[arrival_index].arrival_time)
        _require(
            bool(future) and timestamp == min(future),
            "Event batch skips or invents an arrival/completion timestamp",
        )

        def consume(
            kind: str,
            rid: int,
            sid: int | None,
            batch: tuple[ServerSimulationEvent, ...] = batch,
            timestamp: float = timestamp,
        ) -> None:
            nonlocal offset
            _require(offset < len(batch), "Missing mandatory lifecycle event")
            event = batch[offset]
            offset += 1
            _require(
                (event.event_type, event.request_id, event.server_id) == (kind, rid, sid),
                "Incorrect event order, routing choice, FIFO or lifecycle",
            )
            stage = {
                "request_arrived": 0,
                "request_routed": 1,
                "request_started": 2,
                "request_completed": 3,
            }[kind]
            _require(phases[rid] == stage, "Repeated or out-of-order lifecycle event")
            phases[rid] += 1
            population[stage] -= 1
            population[stage + 1] += 1
            record, sample = records[rid], demand[rid]
            if kind == "request_arrived":
                _require(
                    timestamp == sample.arrival_time == record.arrival_time,
                    "Arrival timestamp mismatch",
                )
            else:
                _require(
                    sid is not None and sid == record.server_id,
                    "Request migrated from its assigned server",
                )
                assert sid is not None
                if kind == "request_routed":
                    _require(
                        timestamp == record.dispatch_time == sample.arrival_time,
                        "Dispatch timestamp mismatch",
                    )
                    queues[sid].append(rid)
                elif kind == "request_started":
                    _require(
                        sid not in active and bool(queues[sid]), "Processing capacity exceeded"
                    )
                    _require(queues[sid].popleft() == rid, "Server FIFO violated")
                    active[sid] = rid
                    starts[rid] = timestamp
                    _require(timestamp == record.processing_start_time, "Start timestamp mismatch")
                    duration = sample.work / rates[sid]
                    scheduled = timestamp + duration
                    _require(
                        math.isfinite(duration)
                        and duration > 0
                        and math.isfinite(scheduled)
                        and scheduled > timestamp,
                        "Unrepresentable processing interval",
                    )
                    _number(record.processing_time, duration, "work/rate processing time")
                    _number(record.processing_end_time, scheduled, "processing completion time")
                else:
                    _require(active.get(sid) == rid, "Completion does not match active request")
                    del active[sid]
                    ends[rid] = timestamp
                    completed[sid] += 1
                    _require(
                        timestamp == record.processing_end_time, "Completion timestamp mismatch"
                    )
                _require(
                    event.server_queue_length_after_event == len(queues[sid]),
                    "Incorrect server queue counter",
                )
                _require(
                    event.server_active_after_event == int(sid in active),
                    "Incorrect server active counter",
                )
            _require(
                event.total_queue_length_after_event == sum(map(len, queues.values())),
                "Incorrect total queue counter",
            )
            _require(
                sum(population) == config.requests
                and min(population) >= 0
                and population[2] == sum(map(len, queues.values()))
                and population[3] == len(active),
                "Request population not conserved",
            )

        # Verify successors against the state rebuilt from evidence, not returned metrics.
        due = sorted(
            (sid, rid)
            for sid, rid in active.items()
            if records[rid].processing_end_time == timestamp
        )
        for sid, rid in due:
            consume("request_completed", rid, sid)
        for sid in ids:
            if sid not in active and queues[sid]:
                consume("request_started", queues[sid][0], sid)
        while arrival_index < len(samples) and samples[arrival_index].arrival_time == timestamp:
            rid = samples[arrival_index].request_id
            arrival_index += 1
            consume("request_arrived", rid, None)
            if config.routing_strategy == "round_robin":
                sid = ids[cursor]
                cursor = (cursor + 1) % len(ids)
            else:
                sid = min(
                    ids, key=lambda server: (len(queues[server]) + int(server in active), server)
                )
            consume("request_routed", rid, sid)
            if sid not in active:
                consume("request_started", queues[sid][0], sid)
        _require(offset == len(batch), "Unexpected events after the settled timestamp")
        _require(
            population[1] == 0 and not any(sid not in active and queues[sid] for sid in ids),
            "Unfinished dispatch or delayed work-conserving start",
        )
        global_peak = max(global_peak, sum(map(len, queues.values())))
        for sid in ids:
            peaks[sid] = max(peaks[sid], len(queues[sid]))

    _require(
        population == [0, 0, 0, 0, config.requests] and not active and not any(queues.values()),
        "Experiment did not drain every request",
    )
    responses = sorted(ends[s.request_id] - s.arrival_time for s in samples)
    waits = [starts[s.request_id] - s.arrival_time for s in samples]
    for sample in samples:
        record = records[sample.request_id]
        _number(record.wait_time, starts[sample.request_id] - sample.arrival_time, "request wait")
        _number(
            record.response_time, ends[sample.request_id] - sample.arrival_time, "request response"
        )
    metrics = result.metrics
    _number(metrics.average_response_time, math.fsum(responses) / len(samples), "mean response")
    _number(metrics.average_wait_time, math.fsum(waits) / len(samples), "mean wait")
    _number(
        metrics.p95_response_time,
        responses[(95 * len(samples) + 99) // 100 - 1],
        "nearest-rank P95",
    )
    _number(metrics.simulation_end_time, max(ends.values()), "experiment completion time")
    _require(metrics.max_queue_length == global_peak, "Incorrect settled global queue peak")
    _require(metrics.completed_requests == sum(completed.values()), "Incorrect completed count")
    for metric in metrics.per_server:
        _require(
            (metric.completed_requests, metric.max_queue_length)
            == (completed[metric.server_id], peaks[metric.server_id]),
            "Incorrect per-server completions or settled queue peak",
        )


def validate_server_comparison(
    result: ServerComparisonResult, expected_request: ServerComparisonRequest | None = None
) -> None:
    result = ServerComparisonResult.model_validate(result)
    request = result.request
    if expected_request is not None:
        _require(
            request == ServerComparisonRequest.model_validate(expected_request),
            "Unexpected comparison design",
        )
    runs = {(run.result.config.seed, run.group_id): run.result for run in result.results}
    groups = tuple(e.group_id for e in request.experiments)
    for seed in request.seeds:
        baseline = runs[seed, groups[0]]
        for experiment in request.experiments:
            run = runs[seed, experiment.group_id]
            validate_server_simulation(run, experiment.config.model_copy(update={"seed": seed}))
            _require(
                (run.request_samples, run.workload_hash, run.workload_source)
                == (baseline.request_samples, baseline.workload_hash, baseline.workload_source),
                "Paired runs must share samples, source and workload hash",
            )
    metric_names = (
        "average_response_time",
        "p95_response_time",
        "average_wait_time",
        "max_queue_length",
        "simulation_end_time",
    )
    for summary in result.summaries:
        for name in metric_names:
            values = [
                float(getattr(runs[seed, summary.group_id].metrics, name)) for seed in request.seeds
            ]
            observed = getattr(summary, name)
            for label, expected in (
                ("mean", math.fsum(values) / len(values)),
                ("minimum", min(values)),
                ("maximum", max(values)),
            ):
                _number(getattr(observed, label), expected, f"{summary.group_id}.{name}.{label}")
    for difference in result.paired_differences:
        baseline_metrics = runs[difference.seed, groups[0]].metrics
        candidate = runs[difference.seed, difference.candidate_group_id].metrics
        for name in metric_names:
            expected = getattr(candidate, name) - getattr(baseline_metrics, name)
            actual = getattr(difference, name + "_difference")
            if name == "max_queue_length":
                _require(actual == expected, "Incorrect paired queue difference")
            else:
                _number(actual, expected, "paired " + name)
    # Derive rankings from runs, not the (already checked) returned summaries.
    means = {
        group: math.fsum(runs[seed, group].metrics.average_response_time for seed in request.seeds)
        / len(request.seeds)
        for group in groups
    }
    ranking = tuple(sorted(groups, key=lambda g: (means[g], groups.index(g))))
    winners = tuple(
        g for g in ranking if math.isclose(means[g], means[ranking[0]], rel_tol=1e-9, abs_tol=1e-9)
    )
    _require(result.ranking_group_ids == ranking, "Incorrect ranking")
    _require(result.winner_group_ids == winners, "Incorrect numerical tie winners")
    _require(
        result.comparison_id
        == _digest(
            {
                "contract": "server-lb-comparison-v1",
                "request": request.model_dump(mode="json"),
                "runs": [
                    {
                        "group_id": r.group_id,
                        "seed": r.result.config.seed,
                        "result_id": r.result.result_id,
                    }
                    for r in result.results
                ],
            }
        ),
        "Incorrect comparison ID",
    )
