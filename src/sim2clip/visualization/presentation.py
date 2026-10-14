"""Versioned public narrative metadata; no simulation or model execution.

Metrics and plan configurations stay in the original validated result. Receipt
binding is checked locally before publishing any claim about its Agent history.
"""

import hashlib
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sim2clip.agent.artifacts import ArtifactStore, json_bytes
from sim2clip.scenario_registry import result_runs, validate_tool_response
from sim2clip.scenarios.server_contracts import ServerComparisonResult
from sim2clip.simulation.schemas import ComparisonResult
from sim2clip.visualization.data import ExperimentResult, parse_result, scenario_id_of
from sim2clip.visualization.timeline import public_call, public_rows

Phase = Literal["question", "plan", "tool", "verify", "simulate", "reveal"]
PHASES: tuple[Phase, ...] = ("question", "plan", "tool", "verify", "simulate", "reveal")
CHECKS = frozenset(
    {
        "event_lifecycle",
        "FIFO",
        "capacity",
        "metrics",
        "request_provenance",
        "paired_samples",
        "comparison_statistics",
    }
)
Metric = Literal[
    "average_wait_time",
    "average_response_time",
    "p95_response_time",
    "max_queue_length",
    "simulation_end_time",
]


class PresentationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PresentationConfig(PresentationModel):
    config_version: Literal["1"] = "1"
    duration_seconds: float = Field(default=30, gt=0)
    phase_durations: tuple[float, float, float, float, float, float] = (3, 3, 2, 2, 14, 6)
    result_scope: Literal["selected_seed"] = "selected_seed"
    default_mode: Literal["analysis", "presentation"] = "analysis"
    profile: Literal["default"] = "default"

    @model_validator(mode="after")
    def durations_match(self) -> Self:
        if (
            any(t <= 0 for t in self.phase_durations)
            or abs(sum(self.phase_durations) - self.duration_seconds) > 1e-9
        ):
            raise ValueError("Positive phase durations must sum to presentation duration")
        return self


class ResultReference(PresentationModel):
    result_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["validated_result"] = "validated_result"


class PublicCall(PresentationModel):
    name: Literal[
        "run_simulation",
        "compare_simulations",
        "run_server_simulation",
        "compare_server_simulations",
    ]
    status: Literal["success", "error", "unknown"]
    cached: bool | None = None
    round: int | None = None
    attempt: int | None = None
    completed_at: str | None = None
    association: Literal["verified", "unverified"] = "unverified"
    response_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    result_ids: tuple[str, ...] = ()


class PublicVerification(PresentationModel):
    origin: Literal["agent", "export"]
    valid: bool | None = None
    sufficient: bool | None = None
    checks: tuple[str, ...] = ()


class PublicExecutionSummary(PresentationModel):
    summary_version: Literal["1"] = "1"
    public_question: str | None = Field(default=None, max_length=180)
    question_source: Literal["explicit_public", "configuration"] = "configuration"
    planner_kind: Literal["real_llm", "offline_demo", "unknown"] = "unknown"
    evidence_completeness: Literal["complete", "partial", "result_only"] = "result_only"
    primary_metric: Metric | None = None
    calls: tuple[PublicCall, ...] = ()
    verification: tuple[PublicVerification, ...] = ()


class PresentationManifest(PresentationModel):
    presentation_version: Literal["1"] = "1"
    scenario: Literal["queue", "server_load_balancing"]
    result_ref: ResultReference
    config: PresentationConfig = PresentationConfig()
    execution: PublicExecutionSummary


class PresentationState(PresentationModel):
    """Runtime derived state, never persisted as a new business protocol."""

    mode: Literal["analysis", "presentation"]
    layout: Literal["desktop", "vertical"]
    presentation_time: float
    simulation_time: float
    phase: Phase
    playing: bool
    selected_run_ids: tuple[str, ...]
    reveal_visible: bool


def state_at(
    config: PresentationConfig, p: float, end_time: float, *, valid: bool = True
) -> tuple[Phase, float, bool]:
    """Absolute-time mapping, also implemented by the embedded JS controller."""
    p = max(0, min(config.duration_seconds, p))
    boundary = 0.0
    phase: Phase = "reveal"
    for name, duration in zip(PHASES, config.phase_durations, strict=True):
        boundary += duration
        if p < boundary:
            phase = name
            break
    start = sum(config.phase_durations[:4])
    t = end_time * max(0, min(1, (p - start) / config.phase_durations[4]))
    return phase, t, phase == "reveal" and valid


def result_reference(result: ExperimentResult) -> ResultReference:
    value = result.model_dump(mode="json")
    return ResultReference(
        result_id=result.comparison_id
        if isinstance(result, (ComparisonResult, ServerComparisonResult))
        else result.result_id,
        sha256=hashlib.sha256(json_bytes(value)).hexdigest(),
    )


def build_manifest(
    result: ExperimentResult,
    *,
    timeline: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    store: ArtifactStore | None = None,
    config: PresentationConfig | None = None,
    public_question: str | None = None,
) -> PresentationManifest:
    result = parse_result(result.model_dump(mode="json"))
    rows = public_rows(timeline or [])
    private = state or {}
    calls: list[PublicCall] = []
    linked_rounds: set[int] = set()
    # Public timelines alone lack a response digest: retain facts, mark linkage unknown.
    sources = (
        private.get("tool_calls", [])
        if state is not None
        else [
            call
            for row in (timeline or [])
            if row.get("node") == "executor" or row.get("name") == "MCP Tool Call"
            for call in row.get("tool_calls", [])
        ]
    )
    for raw in sources:
        clean = public_call(raw)
        if not clean:
            continue
        linked = False
        response_sha = None
        ids: tuple[str, ...] = ()
        if store is not None and clean["status"] == "success":
            try:
                response = parse_result(store.load(raw["response"]))
                validate_tool_response(clean["name"], clean["arguments"], response)
                linked = result_reference(response) == result_reference(result) or any(
                    run.model_dump(mode="json") == result.model_dump(mode="json")
                    for run in result_runs(response)
                )
                if linked:
                    response_sha = raw["response"]["sha256"]
                    ids = tuple(run.result_id for run in result_runs(response))
                    if type(clean["round"]) is int:
                        linked_rounds.add(clean["round"])
            except (ValueError, OSError, KeyError, TypeError):
                pass
        completed = next(
            (
                row["timestamp"]
                for row in rows
                if any(
                    c["id"] == clean["id"] and c["attempt"] == clean["attempt"]
                    for c in row["tool_calls"]
                )
            ),
            None,
        )
        calls.append(
            PublicCall(
                name=clean["name"],
                status=raw["status"] if raw.get("status") in {"success", "error"} else "unknown",
                cached=clean["cached"] if "cached" in raw else None,
                round=clean["round"],
                attempt=clean["attempt"],
                completed_at=completed,
                association="verified" if linked else "unverified",
                response_sha256=response_sha,
                result_ids=ids,
            )
        )
    linked = bool(linked_rounds)
    planner: Literal["real_llm", "offline_demo", "unknown"] = "unknown"
    if linked and any(row["node"] == "planner" and row["round"] in linked_rounds for row in rows):
        if private.get("planner_mode") == "llm":
            planner = "real_llm"
        elif private.get("planner_mode") == "offline_demo":
            planner = "offline_demo"
    verification = [
        PublicVerification(
            origin="export",
            valid=True,
            checks=("event_lifecycle", "FIFO", "capacity", "metrics", "request_provenance")
            + (("paired_samples", "comparison_statistics") if hasattr(result, "seeds") else ()),
        )
    ]
    validation = private.get("validation_result", {})
    if (
        linked
        and private.get("planning_round") in linked_rounds
        and any(
            row["node"] in {"results", "result_validator"}
            and row["round"] == private.get("planning_round")
            for row in rows
        )
    ):
        verification.insert(
            0,
            PublicVerification(
                origin="agent",
                valid=validation.get("valid") if type(validation.get("valid")) is bool else None,
                sufficient=validation.get("sufficient")
                if type(validation.get("sufficient")) is bool
                else None,
                checks=tuple(
                    key
                    for key in validation.get("checks", [])
                    if isinstance(key, str) and key in CHECKS
                ),
            ),
        )
    metric = getattr(result, "primary_metric", None)
    # A locked legal metric from linked evidence takes precedence over the default.
    candidate = (private.get("design") or {}).get("primary_metric")
    supported = {
        "average_response_time",
        "p95_response_time",
        "average_wait_time",
        "max_queue_length",
        "simulation_end_time",
    }
    if (
        linked
        and scenario_id_of(result) == "server_load_balancing"
        and isinstance(candidate, str)
        and candidate in supported
    ):
        metric = candidate
    execution = PublicExecutionSummary(
        public_question=public_question,
        question_source="explicit_public" if public_question else "configuration",
        planner_kind=planner,
        evidence_completeness="complete"
        if planner != "unknown" and len(verification) == 2
        else "partial"
        if rows or calls
        else "result_only",
        primary_metric=metric,
        calls=tuple(calls),
        verification=tuple(verification),
    )
    return PresentationManifest(
        scenario=scenario_id_of(result),
        result_ref=result_reference(result),
        config=config or PresentationConfig(),
        execution=execution,
    )
