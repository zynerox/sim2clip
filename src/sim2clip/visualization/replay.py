"""Event-only state transitions shared by validation and GIF rendering."""

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal

from sim2clip.simulation.schemas import SimulationResult
from sim2clip.visualization.data import VisualizationError

CustomerStatus = Literal["not_arrived", "waiting", "serving", "completed"]


@dataclass(frozen=True)
class ReplaySnapshot:
    timestamp: float
    events_applied: int
    customer_states: dict[int, CustomerStatus]
    queue: tuple[int, ...]
    cashiers: dict[int, int | None]
    completed: int


class EventReplay:
    """Seek on a simulation clock; all equal-time events settle before snapshots."""

    def __init__(self, result: SimulationResult) -> None:
        self.result = result
        self.reset()
        self._verify_evidence()
        self.reset()

    def reset(self) -> None:
        self.cursor = 0
        self.timestamp = -1.0
        self.states: dict[int, CustomerStatus] = {
            sample.customer_id: "not_arrived" for sample in self.result.customer_samples
        }
        self.queue: deque[int] = deque()
        self.cashiers: dict[int, int | None] = {
            cashier: None for cashier in range(1, self.result.config.cashiers + 1)
        }
        self.completed = 0

    def _apply(self) -> None:
        event = self.result.events[self.cursor]
        customer, cashier = event.customer_id, event.cashier_id
        state = self.states[customer]
        if event.event_type == "customer_arrived":
            if state != "not_arrived":
                raise VisualizationError("Duplicate customer arrival")
            self.states[customer] = "waiting"
            self.queue.append(customer)
        elif event.event_type == "customer_started":
            if (
                state != "waiting"
                or not self.queue
                or self.queue[0] != customer
                or cashier is None
                or self.cashiers[cashier] is not None
            ):
                raise VisualizationError("Invalid FIFO order or occupied cashier")
            self.queue.popleft()
            self.states[customer] = "serving"
            self.cashiers[cashier] = customer
        else:
            if state != "serving" or cashier is None or self.cashiers[cashier] != customer:
                raise VisualizationError("Customer finished without matching service")
            self.states[customer] = "completed"
            self.cashiers[cashier] = None
            self.completed += 1
        if len(self.queue) != event.queue_length_after_event:
            raise VisualizationError("Event queue length disagrees with replay")
        self.cursor += 1

    def _verify_evidence(self) -> None:
        records = {record.customer_id: record for record in self.result.customer_records}
        samples = {sample.customer_id: sample for sample in self.result.customer_samples}
        for customer_id, record in records.items():
            sample = samples[customer_id]
            if not math.isclose(record.arrival_time, sample.arrival_time, abs_tol=1e-9) or not (
                math.isclose(record.service_time, sample.service_time, abs_tol=1e-9)
            ):
                raise VisualizationError("Customer record disagrees with shared sample")
        peak = 0
        for index, event in enumerate(self.result.events):
            record, sample = records[event.customer_id], samples[event.customer_id]
            expected = {
                "customer_arrived": sample.arrival_time,
                "customer_started": record.service_start_time,
                "customer_finished": record.service_end_time,
            }[event.event_type]
            if not math.isclose(event.timestamp, expected, rel_tol=0, abs_tol=1e-9):
                raise VisualizationError("Event timestamp disagrees with customer evidence")
            if event.cashier_id is not None and event.cashier_id != record.cashier_id:
                raise VisualizationError("Event cashier disagrees with customer record")
            self._apply()
            if index == len(self.result.events) - 1 or (
                self.result.events[index + 1].timestamp != event.timestamp
            ):
                peak = max(peak, len(self.queue))
        metrics = self.result.metrics
        if self.completed != self.result.config.customers or self.queue:
            raise VisualizationError("Incomplete customer lifecycle")
        if peak != metrics.max_queue_length:
            raise VisualizationError("Peak queue metric disagrees with settled events")
        expected_metrics = {
            "average_wait_time": math.fsum(r.wait_time for r in records.values()) / len(records),
            "average_service_time": math.fsum(r.service_time for r in records.values())
            / len(records),
            "max_wait_time": max(r.wait_time for r in records.values()),
        }
        for key, value in expected_metrics.items():
            if not math.isclose(value, getattr(metrics, key), rel_tol=1e-9, abs_tol=1e-9):
                raise VisualizationError("Metric disagrees with customer records")
        if not math.isclose(
            self.result.events[-1].timestamp, metrics.simulation_end_time, rel_tol=0, abs_tol=1e-9
        ):
            raise VisualizationError("Completion time disagrees with events")

    def seek(self, timestamp: float) -> ReplaySnapshot:
        if not math.isfinite(timestamp) or timestamp < 0:
            raise VisualizationError("Replay time must be finite and nonnegative")
        if timestamp < self.timestamp:
            self.reset()
        while self.cursor < len(self.result.events):
            if self.result.events[self.cursor].timestamp > timestamp:
                break
            self._apply()
        self.timestamp = timestamp
        return ReplaySnapshot(
            timestamp,
            self.cursor,
            dict(self.states),
            tuple(self.queue),
            dict(self.cashiers),
            self.completed,
        )
