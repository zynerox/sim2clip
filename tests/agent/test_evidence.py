"""Schema-valid lies must still fail independent result validation."""

import ast
import copy
from pathlib import Path
from typing import Any

import pytest
from mcp.server import Server
from mcp.types import CallToolResult

from sim2clip.agent import AgentPolicy, open_workflow
from sim2clip.agent.artifacts import ArtifactStore
from sim2clip.agent.mcp_client import SimulationMCPClient, parse_result
from sim2clip.agent.validation import validate_response
from sim2clip.mcp_server.simulation_server import _call_tool, _list_tools, create_server

from .test_workflow import ScriptedPlanner, compare, settings, single


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind",
    [
        "metric",
        "event_queue",
        "sample_hash",
        "result_id",
        "summary",
        "difference",
        "ranking",
        "winner",
    ],
)
async def test_independent_validator_rejects_shape_valid_corruption(kind: str) -> None:
    name, arguments = compare([42, 43])
    async with SimulationMCPClient(create_server()) as client:
        original = await client.call(name, arguments)
    payload = copy.deepcopy(original)
    if kind == "metric":
        payload["results"][0]["metrics"]["average_wait_time"] += 1
    elif kind == "event_queue":
        payload["results"][0]["events"][0]["queue_length_after_event"] += 1
    elif kind == "sample_hash":
        for run in payload["results"]:
            if run["config"]["seed"] == 42:
                run["workload_hash"] = "0" * 64
    elif kind == "result_id":
        payload["comparison_id"] = "0" * 64
    elif kind == "summary":
        summary = payload["summaries"][0]["average_wait_time"]
        for key in ("mean", "minimum", "maximum"):
            summary[key] += 1
    elif kind == "difference":
        payload["paired_differences"][0]["average_wait_time_difference"] += 1
    elif kind == "ranking":
        payload["ranking_cashiers"].reverse()
    else:
        payload["winner_cashiers"] = [1]
    # This deliberately still passes the simulation layer's shared shape schema.
    result = parse_result(name, payload)
    with pytest.raises(ValueError):
        validate_response(name, arguments, result)


@pytest.mark.anyio
async def test_invalid_evidence_replans_and_only_reports_healthy_result(tmp_path: Path) -> None:
    calls = 0

    async def handler(ctx: Any, params: Any) -> CallToolResult:
        nonlocal calls
        calls += 1
        response = await _call_tool(ctx, params)
        if calls == 1:
            assert response.structured_content is not None
            response.structured_content["metrics"]["average_wait_time"] += 999
            # SDK output shape remains valid; Agent must detect the numeric lie.
        return response

    server = Server("corruption-fixture", on_list_tools=_list_tools, on_call_tool=handler)
    planner = ScriptedPlanner([[single()]])
    async with open_workflow(planner, **settings(tmp_path, server=server)) as workflow:
        state = await workflow.run("模拟10位顾客", thread_id="integrity")
    assert state["status"] == "completed" and calls == 2
    assert state["retry_counts"]["results"] == 1
    assert planner.states[1]["validation_result"]["valid"] is False
    assert planner.states[1]["feedback"]["code"] == "INVALID_EVIDENCE"
    assert len(state["simulation_results"]) == 1


@pytest.mark.anyio
async def test_missing_artifact_after_checkpoint_cannot_generate_report(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[single()]]), **settings(tmp_path, interrupt_before=["report"])
    ) as first:
        state = await first.run("模拟10位顾客", thread_id="missing-evidence")
        Path(state["simulation_results"][0]["path"]).unlink()
    async with open_workflow(ScriptedPlanner([]), **settings(tmp_path)) as second:
        state = await second.resume(thread_id="missing-evidence")
    assert state["status"] == "failed"
    assert not state["report"]
    assert "Final evidence unavailable" in state["errors"][-1]["message"]


@pytest.mark.anyio
async def test_completed_receipt_survives_crash_before_node_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def handler(ctx: Any, params: Any) -> CallToolResult:
        nonlocal calls
        calls += 1
        return await _call_tool(ctx, params)

    server = Server("receipt-fixture", on_list_tools=_list_tools, on_call_tool=handler)
    original_save = ArtifactStore.save_response

    def crash_after_save(self: ArtifactStore, run_id: str, call: Any, payload: Any) -> Any:
        original_save(self, run_id, call, payload)
        raise RuntimeError("Injected process crash after durable receipt")

    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, "save_response", crash_after_save)
        async with open_workflow(
            ScriptedPlanner([[single()]]), **settings(tmp_path, server=server)
        ) as first:
            with pytest.raises(RuntimeError, match="Injected process crash"):
                await first.run("模拟10位顾客", thread_id="receipt")
    async with open_workflow(ScriptedPlanner([]), **settings(tmp_path, server=server)) as second:
        state = await second.resume(thread_id="receipt")
    assert state["status"] == "completed"
    assert calls == 1
    assert len(state["tool_calls"]) == 1
    assert state["tool_calls"][0]["cached"] is True
    assert state["customer_runs"] == 10


@pytest.mark.anyio
async def test_replanning_cannot_discard_pilot_seed(tmp_path: Path) -> None:
    planner = ScriptedPlanner([[compare([42])], [compare([43, 44, 45, 46, 47])]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较收银台", thread_id="seed-lock")
    assert state["status"] == "failed"
    assert len(state["tool_calls"]) == 1
    assert "retain previously selected seeds" in state["feedback"]["reason"]


@pytest.mark.anyio
async def test_result_replan_budget_stops_insufficient_evidence(tmp_path: Path) -> None:
    async with open_workflow(
        ScriptedPlanner([[compare([42])]]),
        **settings(tmp_path, policy=AgentPolicy(result_replans=1)),
    ) as workflow:
        state = await workflow.run("比较收银台", thread_id="insufficient-loop")
    assert state["status"] == "failed"
    assert len(state["tool_calls"]) == 2
    assert state["validation_result"]["valid"] is True
    assert state["validation_result"]["sufficient"] is False
    assert not state["report"]


def test_agent_imports_only_shared_simulation_contracts() -> None:
    source = Path(__file__).parents[2] / "src/sim2clip/agent"
    for path in source.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("sim2clip.simulation")
            ):
                assert node.module == "sim2clip.simulation.schemas", path
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("sim2clip.simulation") for alias in node.names
                ), path
