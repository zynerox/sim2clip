"""JSON-only checkpoint state; connections and models belong to the runtime."""

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from sim2clip.scenario_registry import ScenarioId

JSONDict = dict[str, Any]
Status = Literal["planning", "replanning", "executing", "validating", "completed", "failed"]


class SimulationAgentState(TypedDict):
    state_version: Literal["2"]
    scenario_id: ScenarioId | None
    schema_version: Literal["1"]
    user_query: str
    experiment_goal: str
    experiment_plan: JSONDict
    validated_request: list[JSONDict]
    tool_calls: Annotated[list[JSONDict], add]
    simulation_results: list[JSONDict]
    comparison_result: JSONDict | None
    validation_result: JSONDict
    decision_history: Annotated[list[JSONDict], add]
    retry_counts: JSONDict
    status: Status
    run_id: str
    planner_mode: str
    messages: Annotated[list[JSONDict], add]
    errors: Annotated[list[JSONDict], add]
    planning_round: int
    call_cursor: int
    customer_runs: int
    design: JSONDict | None
    feedback: JSONDict
    next_action: str
    policy: JSONDict
    artifacts: JSONDict
    report: str


def initial_state(query: str, run_id: str, policy: JSONDict) -> SimulationAgentState:
    return {
        "state_version": "2",
        "scenario_id": None,
        "schema_version": "1",
        "user_query": query,
        "experiment_goal": "",
        "experiment_plan": {},
        "validated_request": [],
        "tool_calls": [],
        "simulation_results": [],
        "comparison_result": None,
        "validation_result": {},
        "decision_history": [],
        "retry_counts": {"parameters": 0, "tools": 0, "results": 0},
        "status": "planning",
        "run_id": run_id,
        "planner_mode": "",
        "messages": [],
        "errors": [],
        "planning_round": 0,
        "call_cursor": 0,
        "customer_runs": 0,
        "design": None,
        "feedback": {},
        "next_action": "planner",
        "policy": policy,
        "artifacts": {},
        "report": "",
    }
