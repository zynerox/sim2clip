"""Independent per-node FIFO server network on an explicitly phased SimPy clock."""

import math
import platform
from collections import deque
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from statistics import fmean
from typing import Any, Literal

import pydantic
import simpy

from sim2clip.scenarios.server_contracts import (
    PerServerMetrics,
    RequestArrivedEvent,
    RequestCompletedEvent,
    RequestRecord,
    RequestRoutedEvent,
    RequestSample,
    RequestStartedEvent,
    ServerConfig,
    ServerEngineMetadata,
    ServerMetrics,
    ServerSimulationConfig,
    ServerSimulationEvent,
    ServerSimulationResult,
)

from .server_workload import (
    generate_server_workload,
    normalize_server_samples,
    server_workload_hash,
    stable_server_hash,
)

EventName = Literal["request_arrived", "request_routed", "request_started", "request_completed"]
_EVENT_TYPES: dict[
    EventName,
    type[RequestArrivedEvent]
    | type[RequestRoutedEvent]
    | type[RequestStartedEvent]
    | type[RequestCompletedEvent],
] = {
    "request_arrived": RequestArrivedEvent,
    "request_routed": RequestRoutedEvent,
    "request_started": RequestStartedEvent,
    "request_completed": RequestCompletedEvent,
}


@dataclass
class _Server:
    config: ServerConfig
    waiting: deque[RequestSample] = field(default_factory=deque)
    active: RequestSample | None = None
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0
    completed: int = 0
    peak: int = 0


def run_server_simulation(
    config: ServerSimulationConfig, samples: Sequence[RequestSample] | None = None
) -> ServerSimulationResult:
    """Drain finite demand; supplied samples are never presented as seeded draws."""
    config = ServerSimulationConfig.model_validate(config)
    if samples is None:
        return _simulate(config, generate_server_workload(config), "generated")
    return _simulate(config, normalize_server_samples(config, samples), "supplied")


def _simulate(
    config: ServerSimulationConfig,
    ordered: tuple[RequestSample, ...],
    source: Literal["generated", "supplied"],
) -> ServerSimulationResult:
    """Private entry for comparisons to share a prevalidated immutable workload."""
    # Represent already-computed float timestamps exactly on the SimPy clock.
    # A float timeout (target - now) can round twice and miss target by one ULP.
    # Durations and published timestamps still follow M1 float arithmetic.
    env = simpy.Environment()  # starts at integer 0; the first timeout advances to Fraction
    servers = [_Server(s) for s in config.servers]
    events: list[ServerSimulationEvent] = []
    records: dict[int, RequestRecord] = {}
    cursor = 0
    global_peak = 0

    def emit(kind: EventName, sample: RequestSample, node: _Server | None) -> None:
        events.append(
            _EVENT_TYPES[kind].model_validate(
                {
                    "sequence": len(events),
                    "timestamp": float(env.now),
                    "event_type": kind,
                    "request_id": sample.request_id,
                    "server_id": node.config.server_id if node else None,
                    "server_queue_length_after_event": len(node.waiting) if node else None,
                    "server_active_after_event": int(node.active is not None) if node else None,
                    "total_queue_length_after_event": sum(len(s.waiting) for s in servers),
                }
            )
        )

    def start(node: _Server) -> None:
        sample = node.waiting.popleft()
        duration = sample.work / node.config.processing_rate
        start_time = float(env.now)
        end = start_time + duration
        if (
            not math.isfinite(duration)
            or duration <= 0
            or not math.isfinite(end)
            or end <= start_time
        ):
            raise ValueError("Cannot represent a finite positive processing interval")
        node.active, node.start, node.end, node.duration = sample, start_time, end, duration
        emit("request_started", sample, node)

    def coordinate() -> Generator[Any, None, None]:
        nonlocal cursor, global_peak
        index = 0
        batches = 0
        while len(records) < config.requests:
            future = [s.end for s in servers if s.active is not None]
            if index < len(ordered):
                future.append(ordered[index].arrival_time)
            if not future:
                raise RuntimeError("Server model deadlock: no future arrival or completion")
            timestamp = min(future)
            # SimPy 4.1.2 uses numeric addition but annotates delay as int | float.
            delay = Fraction.from_float(timestamp) - Fraction(env.now)
            yield env.timeout(delay)  # type: ignore[arg-type]
            if float(env.now) != timestamp:
                raise ValueError("Cannot represent the scheduled SimPy clock exactly")
            batches += 1
            if batches > 2 * config.requests:
                raise RuntimeError("Server model exceeded its finite batch bound")
            # Phase 1: ALL completions, ordered by server ID (one slot per node).
            for node in servers:
                sample = node.active
                if sample is not None and node.end == timestamp:
                    node.active = None
                    node.completed += 1
                    emit("request_completed", sample, node)
                    records[sample.request_id] = RequestRecord(
                        request_id=sample.request_id,
                        server_id=node.config.server_id,
                        arrival_time=sample.arrival_time,
                        work=sample.work,
                        dispatch_time=sample.arrival_time,
                        processing_start_time=node.start,
                        processing_end_time=timestamp,
                        processing_time=node.duration,
                        wait_time=node.start - sample.arrival_time,
                        response_time=timestamp - sample.arrival_time,
                    )
            # Phase 2: old waiters start before any same-time arrival.
            for node in servers:
                if node.active is None and node.waiting:
                    start(node)
            # Phase 3: arrive, choose, enqueue, immediately start; then next request.
            while index < len(ordered) and ordered[index].arrival_time == timestamp:
                sample = ordered[index]
                index += 1
                emit("request_arrived", sample, None)
                if config.routing_strategy == "round_robin":
                    node = servers[cursor]
                    cursor = (cursor + 1) % len(servers)
                else:
                    node = min(
                        servers,
                        key=lambda s: (
                            len(s.waiting) + int(s.active is not None),
                            s.config.server_id,
                        ),
                    )
                node.waiting.append(sample)
                emit("request_routed", sample, node)
                if node.active is None:
                    start(node)
            # Peaks use only the settled timestamp, never transient enqueue counts.
            global_peak = max(global_peak, sum(len(s.waiting) for s in servers))
            for node in servers:
                node.peak = max(node.peak, len(node.waiting))
        if any(s.active is not None or s.waiting for s in servers) or index != len(ordered):
            raise RuntimeError("Server model ended without draining all demand")

    env.process(coordinate())
    env.run()
    completed = tuple(records[s.request_id] for s in ordered)
    responses = sorted(r.response_time for r in completed)
    metrics = ServerMetrics(
        average_response_time=fmean(responses),
        p95_response_time=responses[math.ceil(0.95 * len(responses)) - 1],
        average_wait_time=fmean(r.wait_time for r in completed),
        max_queue_length=global_peak,
        completed_requests=len(completed),
        simulation_end_time=max(r.processing_end_time for r in completed),
        per_server=tuple(
            PerServerMetrics(
                server_id=s.config.server_id,
                completed_requests=s.completed,
                max_queue_length=s.peak,
            )
            for s in servers
        ),
    )
    metadata = ServerEngineMetadata(
        python_version=platform.python_version(),
        simpy_version=str(simpy.__version__),
        pydantic_version=pydantic.__version__,
    )
    sample_hash = server_workload_hash(ordered)
    result_id = stable_server_hash(
        {
            "contract": "server-lb-result-v1",
            "config": config.model_dump(mode="json"),
            "workload_hash": sample_hash,
            "workload_source": source,
            "metadata": metadata.model_dump(mode="json"),
        }
    )
    return ServerSimulationResult(
        result_id=result_id,
        config=config,
        metadata=metadata,
        workload_source=source,
        workload_hash=sample_hash,
        request_samples=ordered,
        request_records=completed,
        metrics=metrics,
        events=tuple(events),
    )
