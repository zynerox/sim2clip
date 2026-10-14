"""Deterministic presentation coordinates; never change simulation events or timing.

Tracks are indexed once from settled event batches, not accumulated from display
frames. Python is the reference for browser parity and future export adapters;
the Phase 7.2 GIF renderer deliberately remains unchanged.
"""

import json
import math
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from itertools import groupby, islice
from pathlib import Path
from typing import Any

from sim2clip.simulation.schemas import SimulationResult
from sim2clip.visualization.data import VisualizationError
from sim2clip.visualization.replay import CustomerStatus, EventReplay, ReplaySnapshot

Point = tuple[float, float]


def load_design() -> dict[str, Any]:
    return dict(json.loads(Path(__file__).with_name("assets").joinpath("design.json").read_text()))


def queue_position(rank: int, design: dict[str, Any]) -> Point:
    columns = int(design["queue_columns"])
    row, column = divmod(rank, columns)
    if row % 2 == 0:
        column = columns - 1 - column
    return (
        float(design["queue_left"] + column * design["queue_gap_x"]),
        float(design["queue_top"] + row * design["queue_gap_y"]),
    )


def cashier_position(cashier: int, count: int, design: dict[str, Any]) -> Point:
    capacity = int(design["visible_cashiers"])
    visible = min(count, capacity)
    index = (cashier - 1) % capacity
    # Hidden pages have separate logical coordinates; never alias cashier IDs.
    page = (cashier - 1) // capacity
    if visible <= 3:
        return (548.0 + page * float(design["width"]), 225.0 + (index - (visible - 1) / 2) * 92)
    return (500.0 + (index // 3) * 108 + page * float(design["width"]), 133.0 + index % 3 * 92)


@dataclass(frozen=True)
class Motion:
    timestamp: float
    duration: float
    path: tuple[Point, ...]
    status: CustomerStatus
    queue_rank: int | None
    rest_heading: int

    def sample(self, timestamp: float) -> tuple[float, float, int, bool]:
        progress = (
            1.0
            if self.duration <= 0
            else min(1.0, max(0.0, (timestamp - self.timestamp) / self.duration))
        )
        eased = progress * progress * (3 - 2 * progress)
        if progress >= 1:
            return *self.path[-1], self.rest_heading, False
        lengths = [math.dist(a, b) for a, b in zip(self.path, self.path[1:], strict=False)]
        distance = eased * math.fsum(lengths)
        for index, length in enumerate(lengths):
            if distance <= length + 1e-9 and length > 1e-9:
                a, b = self.path[index], self.path[index + 1]
                ratio = min(1.0, max(0.0, distance / length))
                return (
                    a[0] + (b[0] - a[0]) * ratio,
                    a[1] + (b[1] - a[1]) * ratio,
                    -1 if b[0] < a[0] else 1,
                    progress < 1,
                )
            distance -= length
        x, y = self.path[-1]
        return x, y, 1, False


@dataclass(frozen=True)
class CustomerPose:
    customer_id: int
    status: CustomerStatus
    x: float
    y: float
    heading: int
    moving: bool
    opacity: float
    queue_rank: int | None
    cashier_id: int | None
    target_x: float
    target_y: float
    variant: int


@dataclass(frozen=True)
class CashierPose:
    cashier_id: int
    x: float
    y: float
    customer_id: int | None
    visible: bool
    progress: float


@dataclass(frozen=True)
class ScenePose:
    timestamp: float
    entities: tuple[CustomerPose, ...]
    cashiers: tuple[CashierPose, ...]
    queue_length: int
    queue_visible: int
    queue_hidden: int
    serving_visible: int
    serving_hidden: int
    completed: int
    completed_visible: int
    completed_hidden: int
    not_arrived: int


class SceneTimeline:
    """Event-indexed positions with an exact replay snapshot at every seek."""

    def __init__(self, run: SimulationResult, *, end_time: float | None = None) -> None:
        self.run = run
        self.design = load_design()
        self.replay = EventReplay(run)
        self.end_time = run.metrics.simulation_end_time if end_time is None else end_time
        if not math.isfinite(self.end_time) or self.end_time < run.metrics.simulation_end_time:
            raise VisualizationError("Scene horizon must include every simulation event")
        self.records = {r.customer_id: r for r in run.customer_records}
        self.tracks: dict[int, list[Motion]] = {}
        self.starts: dict[int, list[float]] = {}
        self._build()

    @property
    def queue_capacity(self) -> int:
        return int(self.design["queue_columns"] * self.design["queue_rows"])

    def _motion(self, customer: int, timestamp: float) -> Motion | None:
        starts = self.starts.get(customer, [])
        index = bisect_right(starts, timestamp) - 1
        return self.tracks[customer][index] if index >= 0 else None

    def _add(
        self, customer: int, timestamp: float, status: CustomerStatus, rank: int | None = None
    ) -> None:
        design, record = self.design, self.records[customer]
        previous = self._motion(customer, timestamp)
        target = (
            queue_position(rank, design)
            if rank is not None
            else cashier_position(record.cashier_id, self.run.config.cashiers, design)
        )
        if previous and previous.status == status and previous.path[-1] == target:
            return
        entrance = (float(design["entrance"][0]), float(design["entrance"][1]))
        gate = (float(design["queue_gate"][0]), float(design["queue_gate"][1]))
        source: Point = (
            previous.sample(timestamp)[:2]
            if previous
            else entrance
            if record.arrival_time == timestamp
            else queue_position(self.queue_capacity - 1, design)
        )
        duration = float(design["motion_seconds"])
        path: list[Point] = [source]
        if status == "waiting":
            assert rank is not None
            if previous and previous.status == "waiting" and previous.queue_rank is not None:
                path.extend(
                    queue_position(k, design) for k in range(previous.queue_rank, rank - 1, -1)
                )
            else:
                if record.arrival_time == timestamp:
                    path.append(gate)
                path.extend(
                    queue_position(k, design) for k in range(self.queue_capacity - 1, rank - 1, -1)
                )
            duration = min(duration, record.service_start_time - timestamp)
        elif status == "serving":
            aisle = float(design["service_aisle_x"])
            if not previous:
                path.append((aisle, gate[1]))
            else:
                path.append((aisle, source[1]))
            path.extend(((aisle, target[1]), target))
            duration = min(duration, record.service_time * 0.45)
        else:
            # Departure is a display window, not a simulated walking delay.
            lane = float(design["exit"][1]) + (customer % 3 - 1) * 8
            target = (float(design["exit"][0]), lane)
            path.extend(((source[0], lane), target))
            duration = min(duration, self.end_time - timestamp)
        if path[-1] != target:
            path.append(target)
        self.tracks.setdefault(customer, []).append(
            Motion(
                timestamp,
                duration,
                tuple(path),
                status,
                rank,
                -1 if rank is not None and (rank // design["queue_columns"]) % 2 else 1,
            )
        )
        self.starts.setdefault(customer, []).append(timestamp)

    def _build(self) -> None:
        queue: deque[int] = deque()
        active: dict[int, int] = {}
        for timestamp, batch in groupby(self.run.events, key=lambda e: e.timestamp):
            finished = []
            for event in batch:
                if event.event_type == "customer_arrived":
                    queue.append(event.customer_id)
                elif event.event_type == "customer_started":
                    queue.popleft()
                    assert event.cashier_id is not None
                    active[event.cashier_id] = event.customer_id
                else:
                    assert event.cashier_id is not None
                    del active[event.cashier_id]
                    finished.append(event.customer_id)
            # No intermediate, zero-duration queue states become motion tracks.
            for rank, customer in enumerate(islice(queue, self.queue_capacity)):
                self._add(customer, timestamp, "waiting", rank)
            for cashier, customer in sorted(active.items()):
                if cashier <= self.design["visible_cashiers"]:
                    self._add(customer, timestamp, "serving")
            for customer in finished:
                if self.records[customer].cashier_id <= self.design["visible_cashiers"]:
                    self._add(customer, timestamp, "completed")

    def seek(self, timestamp: float, snapshot: ReplaySnapshot | None = None) -> ScenePose:
        snapshot = self.replay.seek(timestamp) if snapshot is None else snapshot
        if snapshot.timestamp != timestamp or not math.isfinite(timestamp) or timestamp < 0:
            raise VisualizationError("Scene and replay clocks must match")
        cashiers = []
        for cashier, customer in snapshot.cashiers.items():
            x, y = cashier_position(cashier, self.run.config.cashiers, self.design)
            record = self.records.get(customer) if customer is not None else None
            progress = (
                min(1.0, max(0.0, (timestamp - record.service_start_time) / record.service_time))
                if record
                else 0.0
            )
            cashiers.append(
                CashierPose(
                    cashier, x, y, customer, cashier <= self.design["visible_cashiers"], progress
                )
            )
        waiting = list(snapshot.queue[: self.queue_capacity])
        serving = [c.customer_id for c in cashiers if c.visible and c.customer_id is not None]
        departures = []
        for customer, record in self.records.items():
            duration = min(
                float(self.design["motion_seconds"]), self.end_time - record.service_end_time
            )
            if (
                record.cashier_id <= self.design["visible_cashiers"]
                and record.service_end_time <= timestamp < record.service_end_time + duration
            ):
                departures.append(customer)
        departures.sort(key=lambda c: (-self.records[c].service_end_time, c))
        departures = departures[: int(self.design["visible_departures"])]
        entities = []
        for customer in waiting + serving + departures:
            motion = self._motion(customer, timestamp)
            assert motion is not None
            x, y, heading, moving = motion.sample(timestamp)
            status = snapshot.customer_states[customer]
            opacity = 1.0
            if status == "completed" and motion.duration > 0:
                fraction = (timestamp - motion.timestamp) / motion.duration
                opacity = min(1.0, max(0.0, (1 - fraction) / 0.25))
            entities.append(
                CustomerPose(
                    customer,
                    status,
                    x,
                    y,
                    heading,
                    moving,
                    opacity,
                    waiting.index(customer) if status == "waiting" else None,
                    self.records[customer].cashier_id if status != "waiting" else None,
                    *motion.path[-1],
                    customer % 4,
                )
            )
        # Tied arrivals can put many real people at the entrance. Aggregate
        # overlapping bodies instead of drawing an unreadable stack of sprites.
        priority = {"serving": 0, "waiting": 1, "completed": 2, "not_arrived": 3}
        entities.sort(key=lambda p: (priority[p.status], p.queue_rank or 0, p.customer_id))
        visible_entities: list[CustomerPose] = []
        for person in entities:
            if person.opacity >= 0.15 and all(
                abs(person.x - other.x) >= self.design["person_separation"]
                or abs(person.y - other.y) >= self.design["person_height"]
                for other in visible_entities
            ):
                visible_entities.append(person)
        shown_waiting = sum(p.status == "waiting" for p in visible_entities)
        shown_serving = sum(p.status == "serving" for p in visible_entities)
        shown_completed = sum(p.status == "completed" for p in visible_entities)
        busy = sum(c.customer_id is not None for c in cashiers)
        return ScenePose(
            timestamp,
            tuple(visible_entities),
            tuple(cashiers),
            len(snapshot.queue),
            shown_waiting,
            len(snapshot.queue) - shown_waiting,
            shown_serving,
            busy - shown_serving,
            snapshot.completed,
            shown_completed,
            snapshot.completed - shown_completed,
            self.run.config.customers - len(snapshot.queue) - busy - snapshot.completed,
        )
