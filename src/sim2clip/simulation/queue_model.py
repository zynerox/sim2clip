"""Independent SimPy model for the official convenience-store rush demo."""

import platform
from collections.abc import Generator, Sequence
from heapq import heapify, heappop, heappush
from math import isfinite
from typing import Literal

import pydantic
import simpy
from simpy.events import Event

from sim2clip import __version__

from .events import EventRecorder
from .metrics import calculate_metrics
from .schemas import (
    CustomerRecord,
    CustomerSample,
    EngineMetadata,
    SimulationConfig,
    SimulationResult,
)
from .workload import generate_workload, normalize_samples, stable_hash, workload_hash


def run_simulation(
    config: SimulationConfig, samples: Sequence[CustomerSample] | None = None
) -> SimulationResult:
    """Run to completion; inject fixed samples for testing or paired experiments.

    Equal arrival times are ordered by customer_id. Each call owns a new
    environment, resource, recorder and cashier pool, so runs cannot leak state.
    """
    config = SimulationConfig.model_validate(config)
    source: Literal["generated", "supplied"] = "generated" if samples is None else "supplied"
    ordered = normalize_samples(
        generate_workload(config) if samples is None else samples, customers=config.customers
    )
    env = simpy.Environment()
    resource = simpy.Resource(env, capacity=config.cashiers)
    recorder = EventRecorder(config.cashiers)
    free_cashiers = list(range(1, config.cashiers + 1))
    heapify(free_cashiers)
    completed: dict[int, CustomerRecord] = {}

    def customer_process(sample: CustomerSample) -> Generator[Event, object, None]:
        yield env.timeout(sample.arrival_time)
        recorder.record(
            "customer_arrived", timestamp=float(env.now), customer_id=sample.customer_id
        )
        with resource.request() as request:
            yield request
            cashier_id = heappop(free_cashiers)
            start = float(env.now)
            scheduled_end = start + sample.service_time
            if not isfinite(scheduled_end) or scheduled_end <= start:
                raise ValueError("Service completion cannot be represented at this timestamp")
            recorder.record(
                "customer_started",
                timestamp=start,
                customer_id=sample.customer_id,
                cashier_id=cashier_id,
            )
            yield env.timeout(sample.service_time)
            end = float(env.now)
            recorder.record(
                "customer_finished",
                timestamp=end,
                customer_id=sample.customer_id,
                cashier_id=cashier_id,
            )
            completed[sample.customer_id] = CustomerRecord(
                customer_id=sample.customer_id,
                cashier_id=cashier_id,
                arrival_time=sample.arrival_time,
                service_time=sample.service_time,
                service_start_time=start,
                service_end_time=end,
                wait_time=start - sample.arrival_time,
            )
            heappush(free_cashiers, cashier_id)

    for sample in ordered:
        env.process(customer_process(sample))
    env.run()
    records = tuple(completed[sample.customer_id] for sample in ordered)
    events = recorder.events
    sample_hash = workload_hash(ordered)
    metadata = EngineMetadata(
        engine_version=__version__,
        python_version=platform.python_version(),
        simpy_version=str(simpy.__version__),
        pydantic_version=pydantic.__version__,
    )
    result_id = stable_hash(
        {
            "config": config.model_dump(mode="json"),
            "workload_hash": sample_hash,
            "workload_source": source,
            "metadata": metadata.model_dump(mode="json"),
        }
    )
    return SimulationResult(
        result_id=result_id,
        config=config,
        metadata=metadata,
        workload_source=source,
        workload_hash=sample_hash,
        customer_samples=ordered,
        customer_records=records,
        metrics=calculate_metrics(records, events),
        events=events,
    )
