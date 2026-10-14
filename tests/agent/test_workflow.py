"""Drive real LangGraph and real MCP protocol; only model decisions are scripted."""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from mcp.server import Server
from mcp.types import CallToolResult, TextContent

from sim2clip.agent import AgentPolicy, DemoPlanner, open_workflow
from sim2clip.mcp_server.simulation_server import _call_tool, _list_tools, create_server


class ScriptedPlanner:
    mode = "test_script"

    def __init__(self, batches: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self.batches = batches
        self.states: list[Any] = []

    async def plan(self, state: Any, tools: Any) -> AIMessage:
        self.states.append(state)
        index = min(len(self.states) - 1, len(self.batches) - 1)
        return AIMessage(
            content="Plan from test fixture",
            tool_calls=[
                {"name": name, "args": arguments, "id": f"fixture-{i}", "type": "tool_call"}
                for i, (name, arguments) in enumerate(self.batches[index])
            ],
        )


def compare(seeds: list[int], *, customers: int = 10) -> tuple[str, dict[str, Any]]:
    return "compare_simulations", {
        "experiments": [{"cashiers": n, "customers": customers} for n in (1, 3)],
        "seeds": seeds,
    }


def single(**args: Any) -> tuple[str, dict[str, Any]]:
    return "run_simulation", {"cashiers": 1, "customers": 10, **args}


def settings(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return {
        "checkpoint_path": tmp_path / "state.sqlite",
        "artifact_root": tmp_path / "artifacts",
        "server": create_server(),
        **kwargs,
    }


@pytest.mark.anyio
async def test_normal_workflow_and_serializable_history(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[compare([42, 43, 44, 45, 46])]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较1与3个收银台", thread_id="normal")
        assert state["status"] == "completed"
        assert state["validation_result"]["valid"] is True
        assert len(state["simulation_results"]) == 10
        assert len(state["tool_calls"]) == 1
        assert state["tool_calls"][0]["status"] == "success"
        assert all("events" not in run for run in state["simulation_results"])
        assert Path(state["artifacts"]["report"]).is_file()
        assert "平均等待" in state["report"]
        json.dumps(state, allow_nan=False)
        history = [s async for s in workflow.graph.aget_state_history(workflow.config("normal"))]
        assert len(history) >= 7
        assert {item["node"] for item in state["decision_history"]} >= {
            "planner",
            "parameter_validator",
            "executor",
            "result_validator",
            "report",
        }


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [{"customers": -100}, {"cashiers": 0}, {"unknown": 1}])
async def test_invalid_parameters_replan_before_any_tool(tmp_path: Path, bad: Any) -> None:
    planner = ScriptedPlanner([[single(**bad)], [single()]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="repair")
    assert state["status"] == "completed"
    assert state["retry_counts"]["parameters"] == 1
    assert state["planning_round"] == 2
    assert len(state["tool_calls"]) == 1
    assert planner.states[1]["feedback"]["code"] == "INVALID_PARAMETERS"
    assert planner.states[1]["tool_calls"] == []


@pytest.mark.anyio
async def test_insufficient_results_trigger_second_tool_round(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[compare([42])], [compare([42, 43, 44, 45, 46])]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较收银台等待", thread_id="evidence")
    assert state["status"] == "completed"
    assert len(state["tool_calls"]) == 2
    assert state["retry_counts"]["results"] == 1
    assert planner.states[1]["feedback"]["code"] == "INSUFFICIENT_EVIDENCE"
    assert len(state["simulation_results"]) == 10  # the pilot seed is deduplicated


@pytest.mark.anyio
async def test_multiple_calls_checkpoint_each_call(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[single(seed=42), single(seed=43)]])
    async with open_workflow(planner, **settings(tmp_path, interrupt_after=["executor"])) as first:
        partial = await first.run("模拟10位顾客", thread_id="batch")
        assert len(partial["tool_calls"]) == 1
        assert partial["call_cursor"] == 1
        assert (await first.graph.aget_state(first.config("batch"))).next == ("executor",)
    # New SQLite connection, MCP session and planner instance.
    unused = ScriptedPlanner([[single(customers=999)]])
    async with open_workflow(unused, **settings(tmp_path)) as second:
        complete = await second.resume(thread_id="batch")
        again = await second.resume(thread_id="batch")
    assert unused.states == []
    assert complete == again
    assert complete["status"] == "completed"
    assert len(complete["tool_calls"]) == 2
    assert [call["id"] for call in complete["tool_calls"]] == ["r1-c0", "r1-c1"]
    assert {run["config"]["seed"] for run in complete["simulation_results"]} == {42, 43}


def error_server(*, retryable: bool, permanent: bool = False) -> Server[dict[str, object]]:
    attempts = 0

    async def call(ctx: Any, params: Any) -> CallToolResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1 or permanent:
            payload = {
                "error": {
                    "code": "TEMPORARY_TOOL_ERROR" if retryable else "ENGINE_ERROR",
                    "message": "Injected failure",
                    "retryable": retryable,
                }
            }
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload))],
                structured_content=payload,
                is_error=True,
            )
        return await _call_tool(ctx, params)

    return Server("fixture", on_list_tools=_list_tools, on_call_tool=call)


@pytest.mark.anyio
async def test_tool_error_recovers_without_replanning_or_fake_metrics(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[single()]])
    async with open_workflow(
        planner, **settings(tmp_path, server=error_server(retryable=True))
    ) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="tool-retry")
    assert state["status"] == "completed"
    assert state["retry_counts"]["tools"] == 1
    assert state["planning_round"] == 1
    assert [call["status"] for call in state["tool_calls"]] == ["error", "success"]
    assert [call["attempt"] for call in state["tool_calls"]] == [1, 2]
    assert state["tool_calls"][0]["arguments"] == state["tool_calls"][1]["arguments"]


@pytest.mark.anyio
async def test_deterministic_engine_failure_stops(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single()]]), **settings(tmp_path, server=error_server(retryable=False))
    ) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="engine-failure")
    assert state["status"] == "failed"
    assert len(state["tool_calls"]) == 1
    assert not state["simulation_results"] and not state["report"]


@pytest.mark.anyio
async def test_retry_budget_stops_infinite_tool_failure(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single()]]),
        **settings(tmp_path, server=error_server(retryable=True, permanent=True)),
    ) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="retry-limit")
    assert state["status"] == "failed"
    assert len(state["tool_calls"]) == 3
    assert state["retry_counts"]["tools"] == 2


@pytest.mark.anyio
async def test_parameter_retry_budget_stops(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single(customers=-1)]]), **settings(tmp_path)
    ) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="bad-loop")
    assert state["status"] == "failed"
    assert state["planning_round"] == 3
    assert not state["tool_calls"]


@pytest.mark.anyio
async def test_cannot_change_workload_to_chase_a_winner(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[compare([42])], [compare([42, 43, 44, 45, 46], customers=11)]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较收银台等待", thread_id="design-lock")
    assert state["status"] == "failed"
    assert len(state["tool_calls"]) == 1
    assert "locked workload" in state["feedback"]["reason"]


@pytest.mark.anyio
async def test_checkpoint_before_tools_resumes_validated_arguments(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single()]]), **settings(tmp_path, interrupt_before=["executor"])
    ) as first:
        state = await first.run("模拟10位顾客", thread_id="before-tool")
        assert state["tool_calls"] == []
        assert state["validated_request"][0]["arguments"]["customers"] == 10
    async with open_workflow(ScriptedPlanner([]), **settings(tmp_path)) as second:
        state = await second.resume(thread_id="before-tool")
    assert state["status"] == "completed"
    assert state["planning_round"] == 1


@pytest.mark.anyio
async def test_no_thread_overwrite_and_unknown_resume(tmp_path: Path) -> None:
    async with open_workflow(ScriptedPlanner([[single()]]), **settings(tmp_path)) as workflow:
        with pytest.raises(ValueError, match="No checkpoint"):
            await workflow.resume(thread_id="missing")
        await workflow.run("模拟10位顾客", thread_id="exists")
        with pytest.raises(ValueError, match="already exists"):
            await workflow.run("other", thread_id="exists")
        with pytest.raises(ValueError, match="thread_id"):
            await workflow.resume(thread_id="../escape")


@pytest.mark.anyio
async def test_offline_demo_is_explicitly_limited(tmp_path: Path) -> None:
    async with open_workflow(DemoPlanner(), **settings(tmp_path)) as workflow:
        state = await workflow.run("模拟200个顾客", thread_id="unsupported-demo")
    assert state["status"] == "failed"
    assert state["planner_mode"] == "offline_demo"
    assert not state["tool_calls"]


@pytest.mark.anyio
async def test_work_budget_rejects_before_mcp(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single()]]),
        **settings(tmp_path, policy=AgentPolicy(maximum_customer_runs=5)),
    ) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="budget")
    assert state["status"] == "failed"
    assert not state["tool_calls"]
