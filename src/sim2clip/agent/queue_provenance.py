"""MCP-only Queue provenance check; preserve the existing standalone Validator."""

import hashlib
import random

from sim2clip.simulation.schemas import ComparisonResult, SimulationResult


def _draw(stream: random.Random, mean: float) -> float:
    value = stream.expovariate(1.0) * mean
    while value == 0:
        value = stream.expovariate(1.0) * mean
    return value


def validate_queue_provenance(name: str, result: SimulationResult | ComparisonResult) -> None:
    runs = result.results if isinstance(result, ComparisonResult) else (result,)
    # Queue comparison historically injects its generated paired samples into
    # each engine run, which legitimately records workload_source=supplied.
    source = "supplied" if name == "compare_simulations" else "generated"
    for run in runs:
        if run.workload_source != source:
            raise ValueError("Unexpected Queue MCP workload source")
        streams = {
            key: random.Random(
                int.from_bytes(
                    hashlib.sha256(
                        f"sim2clip-workload-v1:{run.config.seed}:{key}".encode("ascii")
                    ).digest(),
                    "big",
                )
            )
            for key in ("arrival", "service")
        }

        arrival = 0.0
        for index, sample in enumerate(run.customer_samples):
            if index:
                arrival += _draw(streams["arrival"], run.config.mean_interarrival_time)
            service = _draw(streams["service"], run.config.mean_service_time)
            if (
                sample.customer_id != index
                or sample.arrival_time != arrival
                or sample.service_time != service
            ):
                raise ValueError(
                    "Queue MCP samples do not match declared seed and workload version"
                )
