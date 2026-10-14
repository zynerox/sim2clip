"""Versioned data contracts shared by the engine, future MCP tools, and replay UI.

All durations are seconds. Models are immutable and reject unknown fields;
tuples serialize to ordinary JSON arrays. A valid shape is not a statistical
claim: independent result validation belongs to the future agent workflow.
"""

from math import isclose
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
PositiveFloat = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EventType = Literal["customer_arrived", "customer_started", "customer_finished"]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class SimulationConfig(ContractModel):
    """One finite-population, shared-FIFO-queue convenience-store experiment."""

    schema_version: Literal["1"] = "1"
    simulation_type: Literal["queue"] = "queue"
    scenario: Literal["convenience_store_rush"] = "convenience_store_rush"
    cashiers: Annotated[int, Field(strict=True, ge=1, le=100)]
    customers: Annotated[int, Field(strict=True, ge=1, le=10000)] = 100
    seed: Seed = 42
    arrival_distribution: Literal["exponential"] = "exponential"
    mean_interarrival_time: PositiveFloat = Field(
        default=10.0, description="Mean arrival gap, seconds"
    )
    service_distribution: Literal["exponential"] = "exponential"
    mean_service_time: PositiveFloat = Field(
        default=25.0, description="Mean service duration, seconds"
    )
    time_unit: Literal["second"] = "second"
    queue_discipline: Literal["fifo"] = "fifo"


class CustomerSample(ContractModel):
    """Precomputed demand; independent of cashier capacity and service order."""

    customer_id: NonNegativeInt
    arrival_time: NonNegativeFloat
    service_time: PositiveFloat


class CustomerRecord(ContractModel):
    customer_id: NonNegativeInt
    cashier_id: PositiveInt
    arrival_time: NonNegativeFloat
    service_time: PositiveFloat
    service_start_time: NonNegativeFloat
    service_end_time: PositiveFloat
    wait_time: NonNegativeFloat

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if not self.arrival_time <= self.service_start_time < self.service_end_time:
            raise ValueError("Expected arrival <= service start < service end")
        if not isclose(self.wait_time, self.service_start_time - self.arrival_time, abs_tol=1e-9):
            raise ValueError("wait_time must equal service_start_time - arrival_time")
        if not isclose(
            self.service_time,
            self.service_end_time - self.service_start_time,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("service_time must match the observed service interval")
        return self


class SimulationEvent(ContractModel):
    """Replay by sequence, including all events at the same timestamp.

    Queue length counts arrived customers that have not yet started service.
    A zero-wait customer can enter and leave this state at one timestamp.
    Cashier IDs are 1-based; customer IDs and sequence numbers are 0-based.
    """

    sequence: NonNegativeInt
    timestamp: NonNegativeFloat
    event_type: EventType
    customer_id: NonNegativeInt
    cashier_id: PositiveInt | None = Field(description="Null for arrivals; required for service")
    queue_length_after_event: NonNegativeInt

    @model_validator(mode="after")
    def validate_cashier_presence(self) -> Self:
        if self.event_type == "customer_arrived" and self.cashier_id is not None:
            raise ValueError("Arrival events cannot identify a cashier")
        if self.event_type != "customer_arrived" and self.cashier_id is None:
            raise ValueError("Service events must identify a cashier")
        return self


class SimulationMetrics(ContractModel):
    average_wait_time: NonNegativeFloat
    average_service_time: PositiveFloat
    max_wait_time: NonNegativeFloat
    max_queue_length: NonNegativeInt
    completed_customers: PositiveInt
    simulation_end_time: PositiveFloat


class EngineMetadata(ContractModel):
    engine_version: str
    workload_version: Literal["1"] = "1"
    python_version: str
    simpy_version: str
    pydantic_version: str


class SimulationResult(ContractModel):
    schema_version: Literal["1"] = "1"
    result_id: Digest
    config: SimulationConfig
    metadata: EngineMetadata
    workload_source: Literal["generated", "supplied"]
    workload_hash: Digest
    customer_samples: tuple[CustomerSample, ...]
    customer_records: tuple[CustomerRecord, ...]
    metrics: SimulationMetrics
    events: tuple[SimulationEvent, ...]

    @model_validator(mode="after")
    def validate_result_structure(self) -> Self:
        count = self.config.customers
        if len(self.customer_samples) != count or len(self.customer_records) != count:
            raise ValueError("Sample and record count must match config.customers")
        sample_ids = {sample.customer_id for sample in self.customer_samples}
        record_ids = {record.customer_id for record in self.customer_records}
        if len(sample_ids) != count or record_ids != sample_ids or len(record_ids) != count:
            raise ValueError("Samples and records must have unique, matching customer IDs")
        if self.metrics.completed_customers != count:
            raise ValueError("completed_customers must match config.customers")
        if any(record.cashier_id > self.config.cashiers for record in self.customer_records):
            raise ValueError("Record cashier_id exceeds capacity")
        if len(self.events) != 3 * count:
            raise ValueError("Each customer requires three lifecycle events")
        last_timestamp = -1.0
        for sequence, event in enumerate(self.events):
            if event.sequence != sequence:
                raise ValueError("Event sequence must be contiguous and start at zero")
            if event.timestamp < last_timestamp:
                raise ValueError("Event timestamps must be nondecreasing")
            if event.customer_id not in sample_ids:
                raise ValueError("Event references an unknown customer")
            if event.cashier_id is not None and event.cashier_id > self.config.cashiers:
                raise ValueError("Event cashier_id exceeds capacity")
            last_timestamp = event.timestamp
        return self


class ComparisonRequest(ContractModel):
    """Compare cashier capacity only, using paired seeds and a bounded budget."""

    experiments: Annotated[tuple[SimulationConfig, ...], Field(min_length=2, max_length=8)]
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=10)] = (42, 43, 44, 45, 46)

    @model_validator(mode="after")
    def validate_fairness_and_budget(self) -> Self:
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Comparison seeds must be unique")
        if len({config.cashiers for config in self.experiments}) != len(self.experiments):
            raise ValueError("Comparison requires distinct cashier counts")
        baseline = self.experiments[0].model_dump(exclude={"cashiers", "seed"})
        for config in self.experiments[1:]:
            if config.model_dump(exclude={"cashiers", "seed"}) != baseline:
                raise ValueError("All experiments must use the same workload settings")
        executions = self.experiments[0].customers * len(self.experiments) * len(self.seeds)
        if executions > 100000:
            raise ValueError("Comparison customer-run budget exceeds 100000")
        return self


class MetricSummary(ContractModel):
    mean: NonNegativeFloat
    minimum: NonNegativeFloat
    maximum: NonNegativeFloat

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("Metric summary must satisfy minimum <= mean <= maximum")
        return self


class ExperimentSummary(ContractModel):
    cashiers: PositiveInt
    result_ids: tuple[Digest, ...]
    average_wait_time: MetricSummary
    max_queue_length: MetricSummary
    simulation_end_time: MetricSummary


class PairedDifference(ContractModel):
    """Signed deltas are candidate minus baseline; positive reduction means improvement."""

    seed: Seed
    baseline_result_id: Digest
    candidate_result_id: Digest
    candidate_cashiers: PositiveInt
    average_wait_time_difference: FiniteFloat
    max_queue_length_difference: Annotated[int, Field(strict=True)]
    simulation_end_time_difference: FiniteFloat
    wait_time_reduction_percent: FiniteFloat | None


class ComparisonResult(ContractModel):
    schema_version: Literal["1"] = "1"
    comparison_id: Digest
    request: ComparisonRequest
    baseline_cashiers: PositiveInt
    seeds: tuple[Seed, ...]
    results: tuple[SimulationResult, ...]
    summaries: tuple[ExperimentSummary, ...]
    paired_differences: tuple[PairedDifference, ...]
    ranking_cashiers: tuple[PositiveInt, ...]
    winner_cashiers: tuple[PositiveInt, ...]
    primary_metric: Literal["average_wait_time"] = "average_wait_time"

    @model_validator(mode="after")
    def validate_comparison_structure(self) -> Self:
        configs = self.request.experiments
        if self.seeds != self.request.seeds or self.baseline_cashiers != configs[0].cashiers:
            raise ValueError("Comparison provenance does not match the request")
        expected = {(config.cashiers, seed) for config in configs for seed in self.seeds}
        observed = {(run.config.cashiers, run.config.seed) for run in self.results}
        if observed != expected or len(self.results) != len(expected):
            raise ValueError("Comparison results must cover every cashier/seed pair exactly once")
        cashiers = {config.cashiers for config in configs}
        if {summary.cashiers for summary in self.summaries} != cashiers:
            raise ValueError("Summaries must cover all experiments")
        if len(self.summaries) != len(configs) or set(self.ranking_cashiers) != cashiers:
            raise ValueError("Invalid summaries or ranking")
        if len(self.ranking_cashiers) != len(configs):
            raise ValueError("Ranking cannot repeat cashier groups")
        if not self.winner_cashiers or not set(self.winner_cashiers).issubset(cashiers):
            raise ValueError("Winners must reference compared cashier groups")
        if len(self.paired_differences) != (len(configs) - 1) * len(self.seeds):
            raise ValueError("Expected one difference per candidate and seed")
        for seed in self.seeds:
            runs = [run for run in self.results if run.config.seed == seed]
            if any(
                run.workload_hash != runs[0].workload_hash
                or run.customer_samples != runs[0].customer_samples
                for run in runs[1:]
            ):
                raise ValueError("Compared experiments must share the exact customer samples")
        return self
