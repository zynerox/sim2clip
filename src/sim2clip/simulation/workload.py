"""Generate common random workloads before any cashier scheduling occurs."""

import json
import random
from collections.abc import Sequence
from hashlib import sha256

from .schemas import CustomerSample, SimulationConfig


def stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _random_stream(seed: int, name: str) -> random.Random:
    # Python's hash() is process-randomized; SHA-256 provides stable stream seeds.
    digest = sha256(f"sim2clip-workload-v1:{seed}:{name}".encode("ascii")).digest()
    return random.Random(int.from_bytes(digest, byteorder="big"))


def _positive_exponential(stream: random.Random, mean: float) -> float:
    # expovariate can return exactly zero. Resample instead of rounding durations.
    value = stream.expovariate(1.0) * mean
    while value == 0.0:
        value = stream.expovariate(1.0) * mean
    return value


def generate_workload(config: SimulationConfig) -> tuple[CustomerSample, ...]:
    """Generate customers 0..N-1, with first arrival at zero, in seconds.

    Capacity never affects demand. Independent random streams keep service
    durations fixed when only arrival settings change, and vice versa.
    """
    config = SimulationConfig.model_validate(config)
    arrivals = _random_stream(config.seed, "arrival")
    services = _random_stream(config.seed, "service")
    samples: list[CustomerSample] = []
    arrival_time = 0.0
    for customer_id in range(config.customers):
        if customer_id:
            arrival_time += _positive_exponential(arrivals, config.mean_interarrival_time)
        samples.append(
            CustomerSample(
                customer_id=customer_id,
                arrival_time=arrival_time,
                service_time=_positive_exponential(services, config.mean_service_time),
            )
        )
    return tuple(samples)


def normalize_samples(
    samples: Sequence[CustomerSample], *, customers: int
) -> tuple[CustomerSample, ...]:
    validated = tuple(CustomerSample.model_validate(sample) for sample in samples)
    if len(validated) != customers:
        raise ValueError("Customer sample count must match config.customers")
    if len({sample.customer_id for sample in validated}) != customers:
        raise ValueError("Customer IDs must be unique")
    return tuple(sorted(validated, key=lambda sample: (sample.arrival_time, sample.customer_id)))


def workload_hash(samples: Sequence[CustomerSample]) -> str:
    ordered = normalize_samples(samples, customers=len(samples))
    return stable_hash([sample.model_dump(mode="json") for sample in ordered])
