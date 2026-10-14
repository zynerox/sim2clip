"""Numerical summaries of completed records and replay events; no model calls."""

from collections.abc import Iterator, Sequence
from itertools import groupby
from statistics import fmean

from .schemas import CustomerRecord, SimulationEvent, SimulationMetrics


def settled_queue_lengths(events: Sequence[SimulationEvent]) -> Iterator[tuple[float, int]]:
    """Right-continuous queue snapshots after ALL events at each timestamp.

    Using max(queue_length_after_event) directly would count zero-duration
    transitions at immediate service grants as genuine queue peaks.
    """
    for timestamp, batch in groupby(events, key=lambda event: event.timestamp):
        last: SimulationEvent | None = None
        for event in batch:
            last = event
        if last is not None:
            yield timestamp, last.queue_length_after_event


def calculate_metrics(
    records: Sequence[CustomerRecord], events: Sequence[SimulationEvent]
) -> SimulationMetrics:
    if not records or not events:
        raise ValueError("Metrics require completed customer records and events")
    return SimulationMetrics(
        average_wait_time=fmean(record.wait_time for record in records),
        average_service_time=fmean(
            record.service_end_time - record.service_start_time for record in records
        ),
        max_wait_time=max(record.wait_time for record in records),
        max_queue_length=max(length for _, length in settled_queue_lengths(events)),
        completed_customers=len(records),
        simulation_end_time=max(record.service_end_time for record in records),
    )
