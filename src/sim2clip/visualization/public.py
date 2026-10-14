"""Evidence-only public exports. Raw checkpoints and local audit files stay private."""

from typing import Any

from sim2clip.agent.reporting import queue_report, server_report
from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.simulation.schemas import SimulationResult
from sim2clip.visualization.data import ExperimentResult, runs_of, scenario_id_of
from sim2clip.visualization.timeline import execution_timeline, public_call


def public_context(
    state: dict[str, Any], *, result: ExperimentResult | None = None
) -> dict[str, Any]:
    scene = scenario_id_of(result) if result is not None else state.get("scenario_id")
    server = scene == "server_load_balancing"
    identified = isinstance(scene, str) and scene in {"queue", "server_load_balancing"}
    single = isinstance(result, (SimulationResult, ServerSimulationResult)) or (
        result is None
        and state.get("comparison_result") is None
        and bool(state.get("simulation_results"))
    )
    goal = (
        "未生成已验证的实验结果"
        if not identified
        else "描述单次服务器请求分流的响应时间"
        if server and single
        else "描述单次便利店排队的等待时间"
        if single
        else "比较服务器配置与分流策略对平均响应时间的影响"
        if server
        else "比较收银台容量对平均等待时间的影响"
    )
    return {
        "user_query": "实验请求尚未完成"
        if not identified
        else "服务器请求分流实验"
        if server
        else "便利店有限顾客排队实验",
        "experiment_goal": goal,
        "planner_mode": state.get("planner_mode")
        if state.get("planner_mode") in {"llm", "offline_demo"}
        else "saved_evidence",
        "run_id": state.get("run_id", ""),
    }


def public_report(result: ExperimentResult, state: dict[str, Any]) -> str:
    context = public_context({**state, "scenario_id": scenario_id_of(result)}, result=result)
    if isinstance(result, (ServerSimulationResult, ServerComparisonResult)):
        return server_report([result], context)
    context["design"] = {"workload": runs_of(result)[0].config.model_dump(mode="json")}
    return queue_report([result], context)


def public_state(state: dict[str, Any], report: str = "") -> dict[str, Any]:
    """A presentation reference, not a resumable replacement for the SQLite state."""
    keys = (
        "state_version",
        "schema_version",
        "scenario_id",
        "status",
        "planning_round",
        "retry_counts",
        "simulation_results",
        "comparison_result",
    )
    return {
        **{key: state.get(key) for key in keys},
        **public_context(state),
        "report": report,
        "tool_calls": [
            clean for call in state.get("tool_calls", []) if (clean := public_call(call))
        ],
        "decision_history": [
            {key: row[key] for key in ("node", "round", "action", "reason")}
            for row in execution_timeline([], state)
        ],
        "public_projection": "1",
    }
