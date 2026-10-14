"""LangGraph owns routing and loops; runtime dependencies stay outside State."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sim2clip.agent.nodes import AgentNodes
from sim2clip.agent.state import SimulationAgentState


def route(state: SimulationAgentState) -> str:
    return state["next_action"]


def after_planner(state: SimulationAgentState) -> str:
    if state["status"] == "failed":
        return "end"
    return "planner" if state["status"] == "replanning" else "parameter_validator"


def build_graph(
    nodes: AgentNodes,
    checkpointer: BaseCheckpointSaver[Any],
    *,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
) -> CompiledStateGraph[SimulationAgentState, None, SimulationAgentState, SimulationAgentState]:
    graph = StateGraph(SimulationAgentState)
    graph.add_node("planner", nodes.plan)
    graph.add_node("parameter_validator", nodes.parameters)
    graph.add_node("executor", nodes.execute)
    graph.add_node("result_validator", nodes.results)
    graph.add_node("report", nodes.report)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        after_planner,
        {"parameter_validator": "parameter_validator", "planner": "planner", "end": END},
    )
    graph.add_conditional_edges(
        "parameter_validator", route, {"planner": "planner", "executor": "executor", "end": END}
    )
    graph.add_conditional_edges(
        "executor",
        route,
        {
            "planner": "planner",
            "executor": "executor",
            "result_validator": "result_validator",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "result_validator", route, {"planner": "planner", "report": "report", "end": END}
    )
    graph.add_edge("report", END)
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        name="Sim2Clip",
    )
