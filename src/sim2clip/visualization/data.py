"""Load evidence without running simulations or creating another result schema."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from sim2clip.agent.server_validation import validate_server_comparison, validate_server_simulation
from sim2clip.agent.validation import validate_response
from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.simulation.schemas import ComparisonResult, SimulationResult

QueueResult = SimulationResult | ComparisonResult
ServerResult = ServerSimulationResult | ServerComparisonResult
ExperimentResult = QueueResult | ServerResult


class VisualizationError(ValueError):
    """Incomplete or inconsistent evidence cannot be animated."""


def scenario_id_of(result: ExperimentResult) -> Literal["queue", "server_load_balancing"]:
    return (
        "server_load_balancing"
        if isinstance(result, (ServerSimulationResult, ServerComparisonResult))
        else "queue"
    )


def parse_result(value: Any) -> ExperimentResult:
    try:
        if not isinstance(value, dict):
            raise VisualizationError("Result must be a SimulationResult or ComparisonResult object")
        if value.get("scenario") == "server_load_balancing":
            if value.get("result_kind") == "comparison":
                comparison = ServerComparisonResult.model_validate(value)
                validate_server_comparison(comparison)
                return comparison
            single = ServerSimulationResult.model_validate(value)
            validate_server_simulation(single)
            return single
        if value.get("scenario") not in (None, "convenience_store_rush"):
            raise VisualizationError("Unsupported visualization scenario")
        if "comparison_id" in value:
            queue_comparison = ComparisonResult.model_validate(value)
            validate_response(
                "compare_simulations",
                queue_comparison.request.model_dump(mode="json"),
                queue_comparison,
            )
            return queue_comparison
        queue_single = SimulationResult.model_validate(value)
        validate_response(
            "run_simulation", queue_single.config.model_dump(mode="json"), queue_single
        )
        return queue_single
    except ValidationError as exc:
        raise VisualizationError(
            "Invalid result or missing events; supply the complete MCP result JSON. "
            + str(exc.errors(include_url=False, include_input=False)[0]["msg"])
        ) from None
    except ValueError as exc:
        raise VisualizationError(str(exc)) from None


def load_result(path: Path) -> ExperimentResult:
    try:
        return parse_result(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise VisualizationError("Result file is not valid JSON") from None


def runs_of(result: QueueResult) -> tuple[SimulationResult, ...]:
    return result.results if isinstance(result, ComparisonResult) else (result,)


def select_demo_runs(result: QueueResult) -> tuple[SimulationResult, ...]:
    runs = runs_of(result)
    seed = 42 if any(run.config.seed == 42 for run in runs) else runs[0].config.seed
    groups = (
        [config.cashiers for config in result.request.experiments]
        if isinstance(result, ComparisonResult)
        else [result.config.cashiers]
    )
    return tuple(
        next(r for r in runs if r.config.seed == seed and r.config.cashiers == c)
        for c in groups[:2]
    )
