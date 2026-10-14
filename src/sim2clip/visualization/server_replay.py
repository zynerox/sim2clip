"""Server event reference replay. No simulation execution or animation timing."""

from collections import deque
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from sim2clip.agent.server_validation import validate_server_simulation
from sim2clip.scenarios.server_contracts import ServerSimulationResult
from sim2clip.visualization.data import VisualizationError

RequestStatus = Literal["not_arrived", "arrived", "waiting", "processing", "completed"]


@dataclass(frozen=True)
class ServerSnapshot:
    timestamp: float
    event_cursor: int
    request_states: dict[int, RequestStatus]
    queues: dict[int, tuple[int, ...]]
    active: dict[int, int | None]
    completed_per_server: dict[int, int]
    not_arrived: int
    waiting: int
    processing: int
    completed: int


class ServerEventReplay:
    def __init__(self, result: ServerSimulationResult) -> None:
        try:
            validate_server_simulation(result)
        except ValueError as exc:
            raise VisualizationError(str(exc)) from None
        self.result = result
        self.reset()

    def reset(self) -> None:
        self.cursor = 0
        self.timestamp = -1.0
        self.states: dict[int, RequestStatus] = dict.fromkeys(
            (s.request_id for s in self.result.request_samples), "not_arrived"
        )
        self.queues: dict[int, deque[int]] = {
            s.server_id: deque() for s in self.result.config.servers
        }
        self.active: dict[int, int | None] = dict.fromkeys(self.queues)
        self.completed = dict.fromkeys(self.queues, 0)

    def seek(self, timestamp: float) -> ServerSnapshot:
        if not isfinite(timestamp) or timestamp < 0:
            raise VisualizationError("Replay time must be finite and nonnegative")
        if timestamp < self.timestamp:
            self.reset()
        events = self.result.events
        while self.cursor < len(events) and events[self.cursor].timestamp <= timestamp:
            event = events[self.cursor]
            rid, sid = event.request_id, event.server_id
            if event.event_type == "request_arrived":
                self.states[rid] = "arrived"
            else:
                assert sid is not None
                if event.event_type == "request_routed":
                    self.queues[sid].append(rid)
                    self.states[rid] = "waiting"
                elif event.event_type == "request_started":
                    assert self.queues[sid].popleft() == rid
                    self.active[sid] = rid
                    self.states[rid] = "processing"
                else:
                    self.active[sid] = None
                    self.completed[sid] += 1
                    self.states[rid] = "completed"
            self.cursor += 1
        self.timestamp = timestamp
        return ServerSnapshot(
            timestamp,
            self.cursor,
            dict(self.states),
            {sid: tuple(q) for sid, q in self.queues.items()},
            dict(self.active),
            dict(self.completed),
            sum(s == "not_arrived" for s in self.states.values()),
            sum(map(len, self.queues.values())),
            sum(rid is not None for rid in self.active.values()),
            sum(self.completed.values()),
        )
