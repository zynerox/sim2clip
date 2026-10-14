"""Deterministic server layout and poses sampled from settled event evidence."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from math import isfinite

from sim2clip.scenarios.server_contracts import ServerSimulationResult
from sim2clip.visualization.data import VisualizationError
from sim2clip.visualization.server_replay import ServerEventReplay, ServerSnapshot

QUEUE_VISIBLE = 6
MOTION_SECONDS = 0.35
RECENT_ROUTES = 3


@dataclass(frozen=True)
class RequestPose:
    request_id: int
    server_id: int
    status: str
    x: float
    y: float
    moving: bool
    progress: float


@dataclass(frozen=True)
class ServerPose:
    server_id: int
    rate: float
    y: float
    queue_count: int
    queue_visible: int
    queue_hidden: int
    active_request: int | None
    completed: int
    outstanding: int
    progress: float


@dataclass(frozen=True)
class ServerScenePose:
    timestamp: float
    event_cursor: int
    width: int
    height: int
    entities: tuple[RequestPose, ...]
    servers: tuple[ServerPose, ...]
    not_arrived: int
    waiting: int
    processing: int
    completed: int


def route_position(progress: float, y: float, target_x: float) -> tuple[float, float]:
    """Entry → balancer → distribution trunk → actual server lane."""
    points = ((48.0, 85.0), (165.0, 85.0), (230.0, 85.0), (230.0, y), (target_x, y))
    lengths = [
        abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points[:-1], points[1:], strict=True)
    ]
    distance = sum(lengths) * progress
    for a, b, length in zip(points[:-1], points[1:], lengths, strict=True):
        if length and distance <= length:
            ratio = distance / length
            return a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio
        distance -= length
    return points[-1]


class ServerSceneTimeline:
    def __init__(self, result: ServerSimulationResult) -> None:
        self.result = result
        self.replay = ServerEventReplay(result)
        self.records = {r.request_id: r for r in result.request_records}
        self.height = 160 + 86 * len(result.config.servers)
        self.arrivals = tuple(s.arrival_time for s in result.request_samples)

    def seek(self, timestamp: float, snapshot: ServerSnapshot | None = None) -> ServerScenePose:
        if not isfinite(timestamp) or timestamp < 0:
            raise VisualizationError("Scene time must be finite and nonnegative")
        state = snapshot or self.replay.seek(timestamp)
        if state.timestamp != timestamp:
            raise VisualizationError("Scene and replay clocks differ")
        entities: list[RequestPose] = []
        servers: list[ServerPose] = []
        for index, server in enumerate(self.result.config.servers):
            sid, y = server.server_id, float(172 + 86 * index)
            queue, active = state.queues[sid], state.active[sid]
            progress = 0.0
            if active is not None:
                record = self.records[active]
                progress = min(
                    1.0, (timestamp - record.processing_start_time) / record.processing_time
                )
            servers.append(
                ServerPose(
                    sid,
                    server.processing_rate,
                    y,
                    len(queue),
                    min(QUEUE_VISIBLE, len(queue)),
                    max(0, len(queue) - QUEUE_VISIBLE),
                    active,
                    state.completed_per_server[sid],
                    len(queue) + int(active is not None),
                    progress,
                )
            )
            visible = [
                (rid, "waiting", 422.0 - rank * 23)
                for rank, rid in enumerate(queue[:QUEUE_VISIBLE])
            ]
            if active is not None:
                visible.append((active, "processing", 515.0))
            for rid, status, target in visible:
                record = self.records[rid]
                # Motion takes no model time: status and counters already reflect the event.
                # Stop entry motion before the first actual state transition.
                boundary = (
                    record.processing_start_time
                    if record.wait_time > 0
                    else record.processing_end_time
                )
                duration = min(MOTION_SECONDS, (boundary - record.arrival_time) * 0.45)
                fraction = (
                    min(1.0, (timestamp - record.arrival_time) / duration) if duration > 0 else 1.0
                )
                fraction = fraction * fraction * (3 - 2 * fraction)
                x, cy = route_position(fraction, y, target)
                if status == "processing" and record.wait_time > 0:
                    duration = min(MOTION_SECONDS, record.processing_time * 0.45)
                    fraction = min(1.0, (timestamp - record.processing_start_time) / duration)
                    fraction = fraction * fraction * (3 - 2 * fraction)
                    x, cy = 422.0 + (target - 422.0) * fraction, y
                entities.append(
                    RequestPose(
                        rid,
                        sid,
                        status,
                        x,
                        cy,
                        fraction < 1.0,
                        progress if status == "processing" else 0.0,
                    )
                )
        # Keep new arrivals interpretable even when they join the aggregated FIFO tail.
        # These are existing waiting requests, deducted from the hidden population.
        visible_ids = {entity.request_id for entity in entities}
        stop = bisect_right(self.arrivals, timestamp)
        server_indices = {server.server_id: i for i, server in enumerate(servers)}
        for sample in self.result.request_samples[max(0, stop - RECENT_ROUTES) : stop]:
            record = self.records[sample.request_id]
            duration = min(MOTION_SECONDS, record.wait_time * 0.45)
            if sample.request_id in visible_ids or not (
                duration > 0 and 0 <= timestamp - sample.arrival_time < duration
            ):
                continue
            index = server_indices[record.server_id]
            server_pose = servers[index]
            fraction = (timestamp - sample.arrival_time) / duration
            fraction = fraction * fraction * (3 - 2 * fraction)
            x, y = route_position(fraction, server_pose.y, 286.0)
            entities.append(
                RequestPose(sample.request_id, record.server_id, "waiting", x, y, True, 0.0)
            )
            servers[index] = replace(
                server_pose,
                queue_visible=server_pose.queue_visible + 1,
                queue_hidden=server_pose.queue_hidden - 1,
            )
        return ServerScenePose(
            timestamp,
            state.event_cursor,
            720,
            self.height,
            tuple(entities),
            tuple(servers),
            state.not_arrived,
            state.waiting,
            state.processing,
            state.completed,
        )
