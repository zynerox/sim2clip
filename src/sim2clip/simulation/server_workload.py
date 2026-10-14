"""Server-only demand streams. Routing and resources never enter sample generation."""

import hashlib
import json
import math
import random
from collections.abc import Sequence
from typing import Literal

from sim2clip.scenarios.server_contracts import (
    BatchArrivals,
    ConstantWork,
    RequestSample,
    ServerSimulationConfig,
)


def stable_server_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stream(seed: int, name: Literal["arrival", "work"]) -> random.Random:
    digest = hashlib.sha256(f"sim2clip-server-workload-v1:{seed}:{name}".encode("ascii"))
    return random.Random(int.from_bytes(digest.digest(), "big"))


def _positive_draw(rng: random.Random, mean: float) -> float:
    for _ in range(128):
        value = rng.expovariate(1.0) * mean
        if not math.isfinite(value) or value < 0:
            raise ValueError("Nonfinite or negative exponential sample")
        if value > 0:
            return value
    raise ValueError("Exponential draw remained zero after 128 attempts")


def generate_server_workload(config: ServerSimulationConfig) -> tuple[RequestSample, ...]:
    config = ServerSimulationConfig.model_validate(config)
    arrival_rng, work_rng = _stream(config.seed, "arrival"), _stream(config.seed, "work")
    arrival = config.arrivals.arrival_time if isinstance(config.arrivals, BatchArrivals) else 0.0
    samples: list[RequestSample] = []
    for request_id in range(config.requests):
        if request_id and not isinstance(config.arrivals, BatchArrivals):
            next_arrival = arrival + _positive_draw(
                arrival_rng, config.arrivals.mean_interarrival_time
            )
            if not math.isfinite(next_arrival) or next_arrival <= arrival:
                raise ValueError("Cannot represent a strictly advancing arrival time")
            arrival = next_arrival
        work = (
            config.workload.work
            if isinstance(config.workload, ConstantWork)
            else _positive_draw(work_rng, config.workload.mean_work)
        )
        # The contract rejects out-of-range samples; do not redraw, truncate or clamp.
        samples.append(RequestSample(request_id=request_id, arrival_time=arrival, work=work))
    return tuple(samples)


def normalize_server_samples(
    config: ServerSimulationConfig, samples: Sequence[RequestSample]
) -> tuple[RequestSample, ...]:
    ordered = tuple(
        sorted(
            (RequestSample.model_validate(sample) for sample in samples),
            key=lambda sample: (sample.arrival_time, sample.request_id),
        )
    )
    if len(ordered) != config.requests:
        raise ValueError("Sample count must match requests")
    if len({s.request_id for s in ordered}) != len(ordered):
        raise ValueError("Sample request IDs must be unique")
    return ordered


def server_workload_hash(samples: Sequence[RequestSample]) -> str:
    ordered = sorted(samples, key=lambda s: (s.arrival_time, s.request_id))
    return stable_server_hash(
        {
            "workload_version": "server-workload-1",
            "samples": [s.model_dump(mode="json") for s in ordered],
        }
    )
