"""Small typed adapters for existing graph nodes; no engine imports."""

from sim2clip.agent.state import JSONDict
from sim2clip.agent.validation import experiment_design as queue_design
from sim2clip.scenario_registry import SimulationOutput, result_runs, tool_contract
from sim2clip.scenarios.server_contracts import (
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerSimulationConfig,
)
from sim2clip.simulation.schemas import ComparisonRequest, ComparisonResult, SimulationConfig


def validated_request(name: str, arguments: JSONDict) -> tuple[JSONDict, int]:
    request = tool_contract(name).parse_request(arguments)
    if isinstance(request, SimulationConfig):
        cost = request.customers
    elif isinstance(request, ComparisonRequest):
        cost = request.experiments[0].customers * len(request.experiments) * len(request.seeds)
    elif isinstance(request, ServerSimulationConfig):
        cost = request.requests
    else:
        cost = (
            request.experiments[0].config.requests * len(request.experiments) * len(request.seeds)
        )
    return request.model_dump(mode="json"), cost


def experiment_design(calls: list[JSONDict], goal: str) -> JSONDict:
    scenes = {tool_contract(call["name"]).scenario_id for call in calls}
    if len(scenes) != 1:
        raise ValueError("A plan cannot mix scenarios")
    scene = next(iter(scenes))
    if scene == "queue":
        design = queue_design(calls)
        design.update(
            scenario_id=scene,
            schema_version="1",
            experiment_goal=goal,
            comparison_axis="cashier_capacity",
            primary_metric="average_wait_time",
        )
        return design
    designs: list[JSONDict] = []
    seeds: set[int] = set()
    for call in calls:
        axis: str
        request = tool_contract(call["name"]).parse_request(call["arguments"])
        if isinstance(request, ServerComparisonRequest):
            configs = [e.config for e in request.experiments]
            seeds.update(request.seeds)
            groups = [
                {
                    "group_id": e.group_id,
                    "config": e.config.model_dump(mode="json", exclude={"seed"}),
                }
                for e in request.experiments
            ]
            axis = request.comparison_axis
        elif isinstance(request, ServerSimulationConfig):
            configs = [request]
            seeds.add(request.seed)
            groups = [
                {"group_id": "single", "config": request.model_dump(mode="json", exclude={"seed"})}
            ]
            axis = "single"
        else:
            raise ValueError("Server plan contains a different scene request")
        designs.append(
            {
                "scenario_id": scene,
                "schema_version": "1",
                "experiment_goal": goal,
                "comparison_axis": axis,
                "primary_metric": "average_response_time",
                "workload": configs[0].model_dump(
                    mode="json", exclude={"servers", "routing_strategy", "seed"}
                ),
                "groups": groups,
            }
        )
    if any(design != designs[0] for design in designs[1:]):
        raise ValueError("Every server call must use the same workload and experiment groups")
    return {**designs[0], "seeds": sorted(seeds)}


def is_comparison(design: JSONDict) -> bool:
    return len(design.get("cashiers") or design.get("groups", [])) > 1


def check_design_lock(previous: JSONDict, current: JSONDict) -> None:
    # Legacy Queue designs predate the three metadata fields; never mutate them.
    keys = (
        "scenario_id",
        "schema_version",
        "experiment_goal",
        "comparison_axis",
        "primary_metric",
        "workload",
        "cashiers",
        "groups",
    )
    for key in keys:
        if key in previous and previous[key] != current.get(key):
            raise ValueError(
                f"Replanning changed the locked workload, groups, goal or metric ({key})"
            )
    if not set(previous["seeds"]).issubset(current["seeds"]):
        raise ValueError("Replanning must retain previously selected seeds")


def minimum_seeds(design: JSONDict, policy_minimum: int) -> int:
    workload = design["workload"]
    if (
        design.get("scenario_id") == "server_load_balancing"
        and workload["arrivals"]["mode"] == "batch"
        and workload["workload"]["mode"] == "constant"
    ):
        return 1
    return policy_minimum


def comparison_covers(result: SimulationOutput, design: JSONDict) -> bool:
    if isinstance(result, ComparisonResult):
        return design.get("scenario_id", "queue") == "queue" and set(
            c.cashiers for c in result.request.experiments
        ) == set(design.get("cashiers", []))
    if isinstance(result, ServerComparisonResult):
        return [e.group_id for e in result.request.experiments] == [
            g["group_id"] for g in design["groups"]
        ]
    return False


def comparison_reference(result: SimulationOutput, reference: JSONDict) -> JSONDict | None:
    if isinstance(result, (ComparisonResult, ServerComparisonResult)):
        compact = {
            **reference,
            "comparison_id": result.comparison_id,
            "seeds": list(result.seeds),
            "summaries": [s.model_dump(mode="json") for s in result.summaries],
        }
        if isinstance(result, ComparisonResult):
            compact["winner_cashiers"] = list(result.winner_cashiers)
        else:
            compact.update(
                baseline_group_id=result.baseline_group_id,
                winner_group_ids=list(result.winner_group_ids),
                ranking_group_ids=list(result.ranking_group_ids),
            )
        return compact
    return None


def compact_response(result: SimulationOutput, reference: JSONDict) -> JSONDict:
    comparison = comparison_reference(result, reference)
    if comparison is not None:
        return {k: v for k, v in comparison.items() if k not in ("path", "sha256")}
    run = result_runs(result)[0]
    return {
        "result_id": run.result_id,
        "config": run.config.model_dump(mode="json"),
        "metrics": run.metrics.model_dump(mode="json"),
    }
