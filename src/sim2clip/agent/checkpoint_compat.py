"""Additive projection for pre-M3 checkpoints; never rewrite historical evidence."""

from typing import cast

from sim2clip.agent.state import JSONDict, SimulationAgentState
from sim2clip.scenario_registry import SCENARIOS, ScenarioId, tool_contract


def compatible_state(state: SimulationAgentState) -> SimulationAgentState:
    projected: JSONDict = dict(state)
    if projected.get("state_version") not in (None, "2"):
        raise ValueError("Unsupported Agent state version")
    if projected.get("schema_version") not in (None, "1"):
        raise ValueError("Unsupported scenario schema version")
    observed = {
        tool_contract(call["name"]).scenario_id
        for call in (*state.get("validated_request", []), *state.get("tool_calls", []))
    }
    if len(observed) > 1:
        raise ValueError("Checkpoint contains mixed-scene tool evidence")
    scenario: ScenarioId | None = projected.get("scenario_id")
    if scenario is not None and scenario not in SCENARIOS:
        raise ValueError("Checkpoint scenario is not registered")
    design = projected.get("design")
    if scenario is None and observed:
        scenario = next(iter(observed))
    if scenario is None and design and "cashiers" in design:
        scenario = "queue"
    if scenario is not None and observed and observed != {scenario}:
        raise ValueError("Checkpoint scenario conflicts with tool history")
    if design and design.get("scenario_id", scenario) != scenario:
        raise ValueError("Checkpoint scenario conflicts with locked design")
    if design and design.get("schema_version", "1") != "1":
        raise ValueError("Unsupported locked design schema version")
    if design and "scenario_id" not in design:
        if scenario != "queue":
            raise ValueError("Unversioned design is not a legacy Queue checkpoint")
        projected["design"] = {
            **design,
            "scenario_id": "queue",
            "schema_version": "1",
            "experiment_goal": state["experiment_goal"],
            "comparison_axis": "cashier_capacity",
            "primary_metric": "average_wait_time",
        }
    projected.update(state_version="2", scenario_id=scenario, schema_version="1")
    return cast(SimulationAgentState, projected)


def compatibility_fields(state: SimulationAgentState) -> JSONDict:
    return {
        "state_version": state["state_version"],
        "scenario_id": state["scenario_id"],
        "schema_version": state["schema_version"],
        "design": state["design"],
    }
