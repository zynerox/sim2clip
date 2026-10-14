"""Immutable server v1 contracts. Shape validation does not certify evidence."""

from math import isfinite, ulp
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def numeric_equal(a: float, b: float) -> bool:
    """M1 tolerance for recomputed numbers, never for identity or event grouping."""
    return isfinite(a) and isfinite(b) and abs(a - b) <= max(1e-9, 4 * max(ulp(a), ulp(b)))


RequestCount = Annotated[int, Field(strict=True, ge=1, le=10000)]
Count = Annotated[int, Field(strict=True, ge=0, le=10000)]
RequestId = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
ServerId = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
SequenceNumber = Annotated[int, Field(strict=True, ge=0, le=39999)]
SingleCapacity = Annotated[int, Field(strict=True, ge=1, le=1)]
ActiveCount = Annotated[int, Field(strict=True, ge=0, le=1)]
ActiveOne = Annotated[int, Field(strict=True, ge=1, le=1)]
ActiveZero = Annotated[int, Field(strict=True, ge=0, le=0)]
ArrivalTime = Annotated[float, Field(strict=True, ge=0, le=1_000_000_000, allow_inf_nan=False)]
Time = Annotated[float, Field(strict=True, ge=0, le=10_001_000_000_000, allow_inf_nan=False)]
PositiveTime = Annotated[
    float, Field(strict=True, gt=0, le=10_001_000_000_000, allow_inf_nan=False)
]
ProcessingTime = Annotated[float, Field(strict=True, gt=0, le=1_000_000_000, allow_inf_nan=False)]
MeanArrival = Annotated[float, Field(strict=True, ge=0.001, le=10000, allow_inf_nan=False)]
MeanWork = Annotated[float, Field(strict=True, ge=0.001, le=10000, allow_inf_nan=False)]
Work = Annotated[float, Field(strict=True, gt=0, le=1_000_000, allow_inf_nan=False)]
Rate = Annotated[float, Field(strict=True, ge=0.001, le=1_000_000, allow_inf_nan=False)]
SignedTime = Annotated[
    float,
    Field(strict=True, ge=-10_001_000_000_000, le=10_001_000_000_000, allow_inf_nan=False),
]
SignedCount = Annotated[int, Field(strict=True, ge=-10000, le=10000)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
GroupId = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
VersionString = Annotated[str, Field(strict=True, min_length=1, max_length=64)]
RoutingStrategy = Literal["round_robin", "least_outstanding"]


class ServerContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class ServerConfig(ServerContractModel):
    server_id: ServerId
    processing_rate: Rate = 1.0
    capacity: SingleCapacity = 1


class ExponentialArrivals(ServerContractModel):
    mode: Literal["exponential"] = "exponential"
    mean_interarrival_time: MeanArrival = 0.01


class BatchArrivals(ServerContractModel):
    mode: Literal["batch"] = "batch"
    arrival_time: ArrivalTime = 0.0


ArrivalConfig = Annotated[ExponentialArrivals | BatchArrivals, Field(discriminator="mode")]


class ExponentialWork(ServerContractModel):
    mode: Literal["exponential"] = "exponential"
    mean_work: MeanWork = 0.025


class ConstantWork(ServerContractModel):
    mode: Literal["constant"] = "constant"
    work: Work = 0.025


WorkConfig = Annotated[ExponentialWork | ConstantWork, Field(discriminator="mode")]


class ServerSimulationConfig(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    simulation_type: Literal["server_queue_network"] = "server_queue_network"
    requests: RequestCount = 1000
    servers: Annotated[tuple[ServerConfig, ...], Field(min_length=1, max_length=32)] = (
        ServerConfig(server_id=1),
    )
    routing_strategy: RoutingStrategy = "round_robin"
    seed: Seed = 42
    arrivals: ArrivalConfig = Field(default_factory=ExponentialArrivals)
    workload: WorkConfig = Field(default_factory=ExponentialWork)
    time_unit: Literal["second"] = "second"
    work_unit: Literal["normalized_work"] = "normalized_work"
    queue_discipline: Literal["fifo"] = "fifo"
    scheduling_policy: Literal["complete_then_dispatch"] = "complete_then_dispatch"

    @model_validator(mode="after")
    def validate_servers(self) -> Self:
        ids = tuple(s.server_id for s in self.servers)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Server IDs must be unique and strictly ascending")
        return self


class ServerExperiment(ServerContractModel):
    group_id: GroupId
    config: ServerSimulationConfig


class ServerComparisonRequest(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    comparison_axis: Literal["routing_strategy", "server_pool"]
    experiments: Annotated[tuple[ServerExperiment, ...], Field(min_length=2, max_length=8)]
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=10)] = (42, 43, 44, 45, 46)

    @model_validator(mode="after")
    def validate_design(self) -> Self:
        if self.seeds != tuple(sorted(set(self.seeds))):
            raise ValueError("Seeds must be unique and strictly ascending")
        if len({e.group_id for e in self.experiments}) != len(self.experiments):
            raise ValueError("Group IDs must be unique")
        base = self.experiments[0].config
        seen: set[str] = set()
        for experiment in self.experiments:
            config = experiment.config
            if config.seed != self.seeds[0]:
                raise ValueError("Each config.seed must equal seeds[0]")
            if (config.requests, config.arrivals, config.workload) != (
                base.requests,
                base.arrivals,
                base.workload,
            ):
                raise ValueError("Experiments must share workload settings")
            if self.comparison_axis == "routing_strategy" and config.servers != base.servers:
                raise ValueError("Strategy comparisons must keep the same server pool")
            if (
                self.comparison_axis == "server_pool"
                and config.routing_strategy != base.routing_strategy
            ):
                raise ValueError("Pool comparisons must keep the same strategy")
            key = config.model_dump_json()
            if key in seen:
                raise ValueError("Duplicate experiment configurations")
            seen.add(key)
        if base.requests * len(self.experiments) * len(self.seeds) > 100000:
            raise ValueError("Request-run budget exceeds 100000")
        return self


class RequestSample(ServerContractModel):
    request_id: RequestId
    arrival_time: ArrivalTime
    work: Work


class RequestRecord(ServerContractModel):
    request_id: RequestId
    server_id: ServerId
    arrival_time: ArrivalTime
    work: Work
    dispatch_time: ArrivalTime
    processing_start_time: Time
    processing_end_time: PositiveTime
    processing_time: ProcessingTime
    wait_time: Time
    response_time: PositiveTime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if not self.arrival_time == self.dispatch_time <= self.processing_start_time:
            raise ValueError("Expected arrival = dispatch <= start")
        if not self.processing_start_time < self.processing_end_time:
            raise ValueError("Expected start < end")
        relationships = (
            (self.wait_time, self.processing_start_time - self.arrival_time),
            (self.response_time, self.processing_end_time - self.arrival_time),
            (self.processing_end_time, self.processing_start_time + self.processing_time),
        )
        if not all(numeric_equal(a, b) for a, b in relationships):
            raise ValueError("Inconsistent derived request times")
        return self


class ServerEventBase(ServerContractModel):
    sequence: SequenceNumber
    timestamp: Time
    request_id: RequestId
    total_queue_length_after_event: Count


class RequestArrivedEvent(ServerEventBase):
    event_type: Literal["request_arrived"]
    server_id: None
    server_queue_length_after_event: None
    server_active_after_event: None


class ServerBoundEvent(ServerEventBase):
    server_id: ServerId
    server_queue_length_after_event: Count


class RequestRoutedEvent(ServerBoundEvent):
    event_type: Literal["request_routed"]
    server_active_after_event: ActiveCount


class RequestStartedEvent(ServerBoundEvent):
    event_type: Literal["request_started"]
    server_active_after_event: ActiveOne


class RequestCompletedEvent(ServerBoundEvent):
    event_type: Literal["request_completed"]
    server_active_after_event: ActiveZero


ServerSimulationEvent = Annotated[
    RequestArrivedEvent | RequestRoutedEvent | RequestStartedEvent | RequestCompletedEvent,
    Field(discriminator="event_type"),
]


class PerServerMetrics(ServerContractModel):
    server_id: ServerId
    completed_requests: Count
    max_queue_length: Count


class ServerMetrics(ServerContractModel):
    average_response_time: PositiveTime
    p95_response_time: PositiveTime
    average_wait_time: Time
    max_queue_length: Count
    completed_requests: RequestCount
    simulation_end_time: PositiveTime
    per_server: Annotated[tuple[PerServerMetrics, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        ids = tuple(m.server_id for m in self.per_server)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Per-server metrics must have unique ascending IDs")
        if sum(m.completed_requests for m in self.per_server) != self.completed_requests:
            raise ValueError("Per-server completion counts must sum to completed_requests")
        if max(self.max_queue_length, *(m.max_queue_length for m in self.per_server)) > (
            self.completed_requests
        ):
            raise ValueError("Queue peaks cannot exceed request count")
        return self


class ServerEngineMetadata(ServerContractModel):
    server_model_version: Literal["server-lb-1"] = "server-lb-1"
    workload_version: Literal["server-workload-1"] = "server-workload-1"
    routing_rules_version: Literal["server-routing-1"] = "server-routing-1"
    schedule_version: Literal["server-schedule-1"] = "server-schedule-1"
    hash_version: Literal["sha256-json-v1"] = "sha256-json-v1"
    python_version: VersionString
    simpy_version: VersionString
    pydantic_version: VersionString


class ServerSimulationResult(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    result_kind: Literal["single"] = "single"
    result_id: Digest
    config: ServerSimulationConfig
    metadata: ServerEngineMetadata
    workload_source: Literal["generated", "supplied"]
    workload_hash: Digest
    request_samples: Annotated[tuple[RequestSample, ...], Field(min_length=1, max_length=10000)]
    request_records: Annotated[tuple[RequestRecord, ...], Field(min_length=1, max_length=10000)]
    metrics: ServerMetrics
    events: Annotated[tuple[ServerSimulationEvent, ...], Field(min_length=4, max_length=40000)]

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        n = self.config.requests
        if len(self.request_samples) != n or len(self.request_records) != n:
            raise ValueError("Sample and record count must equal requests")
        keys = tuple((s.arrival_time, s.request_id) for s in self.request_samples)
        ids = {s.request_id for s in self.request_samples}
        if len(ids) != n or keys != tuple(sorted(keys)):
            raise ValueError("Samples must have unique IDs and canonical order")
        if tuple((r.arrival_time, r.request_id) for r in self.request_records) != keys:
            raise ValueError("Records must exactly cover samples in canonical order")
        servers = tuple(s.server_id for s in self.config.servers)
        for sample, record in zip(self.request_samples, self.request_records, strict=True):
            if sample.work != record.work or record.server_id not in servers:
                raise ValueError("Record sample or server reference mismatch")
        if self.metrics.completed_requests != n:
            raise ValueError("Successful results must complete every request")
        if tuple(m.server_id for m in self.metrics.per_server) != servers:
            raise ValueError("Per-server metrics must cover the configured pool")
        if len(self.events) != 4 * n:
            raise ValueError("Exactly four events per request are required")
        counts: dict[int, dict[str, int]] = {rid: {} for rid in ids}
        previous = -1.0
        for sequence, event in enumerate(self.events):
            if event.sequence != sequence or event.timestamp < previous:
                raise ValueError("Events require consecutive sequence and monotonic timestamps")
            previous = event.timestamp
            if event.request_id not in ids or (
                event.server_id is not None and event.server_id not in servers
            ):
                raise ValueError("Unknown event request or server")
            if event.total_queue_length_after_event > n or (
                event.server_queue_length_after_event is not None
                and event.server_queue_length_after_event > n
            ):
                raise ValueError("Event queue count exceeds requests")
            per_request = counts[event.request_id]
            per_request[event.event_type] = per_request.get(event.event_type, 0) + 1
        expected = dict.fromkeys(
            ("request_arrived", "request_routed", "request_started", "request_completed"), 1
        )
        if any(count != expected for count in counts.values()):
            raise ValueError("Each request requires one event of each lifecycle type")
        return self


class ServerMetricSummary(ServerContractModel):
    mean: Time
    minimum: Time
    maximum: Time

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("Expected minimum <= mean <= maximum")
        return self


class ServerExperimentSummary(ServerContractModel):
    group_id: GroupId
    result_ids: Annotated[tuple[Digest, ...], Field(min_length=1, max_length=10)]
    average_response_time: ServerMetricSummary
    p95_response_time: ServerMetricSummary
    average_wait_time: ServerMetricSummary
    max_queue_length: ServerMetricSummary
    simulation_end_time: ServerMetricSummary

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.max_queue_length.maximum > 10000:
            raise ValueError("Queue summary exceeds request limit")
        if any(
            s.minimum <= 0
            for s in (self.average_response_time, self.p95_response_time, self.simulation_end_time)
        ):
            raise ValueError("Response and completion summaries must be positive")
        return self


class ServerPairedDifference(ServerContractModel):
    seed: Seed
    candidate_group_id: GroupId
    baseline_result_id: Digest
    candidate_result_id: Digest
    average_response_time_difference: SignedTime
    p95_response_time_difference: SignedTime
    average_wait_time_difference: SignedTime
    max_queue_length_difference: SignedCount
    simulation_end_time_difference: SignedTime


class ServerComparisonRun(ServerContractModel):
    group_id: GroupId
    result: ServerSimulationResult


class ServerComparisonResult(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    result_kind: Literal["comparison"] = "comparison"
    comparison_id: Digest
    request: ServerComparisonRequest
    baseline_group_id: GroupId
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=10)]
    results: Annotated[tuple[ServerComparisonRun, ...], Field(min_length=2, max_length=80)]
    summaries: Annotated[tuple[ServerExperimentSummary, ...], Field(min_length=2, max_length=8)]
    paired_differences: Annotated[
        tuple[ServerPairedDifference, ...], Field(min_length=1, max_length=70)
    ]
    ranking_group_ids: Annotated[tuple[GroupId, ...], Field(min_length=2, max_length=8)]
    winner_group_ids: Annotated[tuple[GroupId, ...], Field(min_length=1, max_length=8)]
    primary_metric: Literal["average_response_time"] = "average_response_time"

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        groups = tuple(e.group_id for e in self.request.experiments)
        if self.seeds != self.request.seeds or self.baseline_group_id != groups[0]:
            raise ValueError("Comparison seeds or baseline mismatch")
        keys = tuple((seed, group) for seed in self.seeds for group in groups)
        if tuple((r.result.config.seed, r.group_id) for r in self.results) != keys:
            raise ValueError("Runs must cover every seed/group once in canonical order")
        configs = {e.group_id: e.config for e in self.request.experiments}
        runs = {(r.result.config.seed, r.group_id): r.result for r in self.results}
        for run in self.results:
            expected = configs[run.group_id].model_copy(update={"seed": run.result.config.seed})
            if run.result.config != expected:
                raise ValueError("Run config does not match effective experiment config")
        if tuple(s.group_id for s in self.summaries) != groups:
            raise ValueError("Summaries must cover groups in request order")
        for summary in self.summaries:
            if summary.result_ids != tuple(
                runs[seed, summary.group_id].result_id for seed in self.seeds
            ):
                raise ValueError("Summary result references mismatch")
            if summary.max_queue_length.maximum > configs[summary.group_id].requests:
                raise ValueError("Queue summary exceeds request count")
        if len(self.ranking_group_ids) != len(groups) or set(self.ranking_group_ids) != set(groups):
            raise ValueError("Ranking must cover all groups without duplicates")
        winners = self.winner_group_ids
        if len(winners) != len(set(winners)) or not set(winners) <= set(groups):
            raise ValueError("Winner groups must be unique known groups")
        if winners != tuple(g for g in self.ranking_group_ids if g in winners):
            raise ValueError("Winners must follow ranking order")
        differences = tuple((seed, group) for seed in self.seeds for group in groups[1:])
        if tuple((d.seed, d.candidate_group_id) for d in self.paired_differences) != differences:
            raise ValueError("Paired differences must cover each seed/candidate in order")
        for delta in self.paired_differences:
            if (
                delta.baseline_result_id != runs[delta.seed, groups[0]].result_id
                or delta.candidate_result_id != runs[delta.seed, delta.candidate_group_id].result_id
            ):
                raise ValueError("Paired difference result references mismatch")
        return self
