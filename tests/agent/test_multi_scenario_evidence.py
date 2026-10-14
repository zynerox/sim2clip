"""Server-specific evidence, receipts, recovery and adversarial checkpoint summaries."""

import copy
import json
from pathlib import Path

import pytest
from mcp.server import Server

from sim2clip.agent import ModelPlanner, open_workflow
from sim2clip.agent.artifacts import ArtifactStore, json_bytes
from sim2clip.agent.checkpoint_compat import compatible_state
from sim2clip.agent.mcp_client import SimulationMCPClient
from sim2clip.mcp_server.simulation_server import _call_tool, _list_tools
from sim2clip.scenario_registry import validate_tool_response
from sim2clip.scenarios.server_contracts import ServerSimulationConfig
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.simulation.server_workload import generate_server_workload

from .test_multi_scenario import NativeToolModel, native_plan, server_arguments
from .test_workflow import ScriptedPlanner, settings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["metric", "event", "record", "summary", "difference", "hash"])
async def test_server_corruption_replans_then_reports_only_verified_evidence(tmp_path, kind):
    count = 0

    async def handler(ctx, params):
        nonlocal count
        count += 1
        response = await _call_tool(ctx, params)
        if count == 1:
            payload = response.structured_content
            run = payload["results"][0]["result"]
            if kind == "metric":
                run["metrics"]["average_response_time"] += 1
            elif kind == "event":
                run["events"][0]["total_queue_length_after_event"] += 1
            elif kind == "record":
                run["request_records"][0]["processing_start_time"] += 1e-10
                run["request_records"][0]["processing_end_time"] += 1e-10
            elif kind == "summary":
                for key in ("mean", "minimum", "maximum"):
                    payload["summaries"][0]["average_response_time"][key] += 1
            elif kind == "difference":
                payload["paired_differences"][0]["average_response_time_difference"] += 1
            else:
                run["workload_hash"] = "0" * 64
        return response

    server = Server("server-corruption", on_list_tools=_list_tools, on_call_tool=handler)
    model = NativeToolModel(
        responses=[native_plan("compare_server_simulations", server_arguments())]
    )
    async with open_workflow(ModelPlanner(model), **settings(tmp_path, server=server)) as workflow:
        state = await workflow.run("比较30个请求的服务器分流", thread_id="corruption")
    assert state["status"] == "completed", state["errors"]
    assert count == 2 and state["retry_counts"]["results"] == 1
    assert "INVALID_EVIDENCE" in model.received[1][0].content
    assert len(state["simulation_results"]) == 10
    assert state["validation_result"]["valid"]


@pytest.mark.parametrize("boundary", ["result_validator", "report"])
@pytest.mark.parametrize(
    "kind",
    [
        "metrics",
        "hash",
        "history",
        "ids",
        "receipt",
        "design",
        "comparison",
        "scene_ref",
        "extra_ref",
        "tool_ref",
        "work_budget",
        "call_budget",
        "cost",
    ],
)
async def test_modified_checkpoint_or_receipt_cannot_generate_server_report(
    tmp_path, boundary, kind
):
    planner = ScriptedPlanner([[("compare_server_simulations", server_arguments())]])
    async with open_workflow(
        planner, **settings(tmp_path, interrupt_before=[boundary])
    ) as workflow:
        state = await workflow.run("比较服务器分流", thread_id="modified")
        changed = copy.deepcopy(state)
        if kind == "metrics":
            changed["simulation_results"][0]["metrics"]["average_response_time"] += 1
        elif kind == "hash":
            changed["simulation_results"][0]["sha256"] = "0" * 64
        elif kind == "history":
            # Reducer preserves real history; appending a forged success must be rejected too.
            forged = copy.deepcopy(changed["tool_calls"][0])
            forged["arguments"]["seeds"] = [42]
            changed["tool_calls"] = [forged]
        elif kind == "ids":
            forged = copy.deepcopy(changed["tool_calls"][0])
            forged["result_ids"] = []
            changed["tool_calls"] = [forged]
        elif kind == "receipt":
            receipt = next((tmp_path / "artifacts/modified").glob("*.receipt.json"))
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["arguments"]["seeds"] = [42]
            receipt.write_bytes(json_bytes(payload))
        elif kind == "design":
            changed["design"]["groups"][0]["config"]["servers"][0]["processing_rate"] = 2
        elif kind == "extra_ref":
            forged = copy.deepcopy(changed["simulation_results"][0])
            forged["result_id"] = "0" * 64
            changed["simulation_results"].append(forged)
        elif kind == "scene_ref":
            changed["simulation_results"][0]["scenario_id"] = "queue"
        elif kind == "tool_ref":
            changed["comparison_result"]["tool_name"] = "compare_simulations"
        elif kind == "work_budget":
            changed["customer_runs"] += 1
        elif kind == "call_budget":
            changed["policy"]["maximum_tool_calls"] = 0
        elif kind == "cost":
            forged = copy.deepcopy(changed["tool_calls"][0])
            forged["customer_runs"] -= 1
            changed["tool_calls"] = [forged]
        else:
            changed["comparison_result"]["summaries"][0]["average_response_time"]["mean"] += 1
        update = {key: value for key, value in changed.items() if value != state[key]}
        if update:
            await workflow.graph.aupdate_state(workflow.config("modified"), update)
    async with open_workflow(ScriptedPlanner([]), **settings(tmp_path)) as workflow:
        resumed = await workflow.resume(thread_id="modified")
    assert resumed["status"] == "failed" and not resumed["report"]
    if boundary == "result_validator":
        assert resumed["validation_result"]["valid"] is False
    else:
        assert "Final evidence unavailable" in resumed["errors"][-1]["message"]


async def test_server_durable_receipt_is_used_after_crash_before_checkpoint(tmp_path, monkeypatch):
    count = 0

    async def handler(ctx, params):
        nonlocal count
        count += 1
        return await _call_tool(ctx, params)

    server = Server("server-crash", on_list_tools=_list_tools, on_call_tool=handler)
    save = ArtifactStore.save_response

    def crash(self, run_id, call, payload):
        save(self, run_id, call, payload)
        raise RuntimeError("Injected crash after server receipt fsync")

    planner = ScriptedPlanner([[("run_server_simulation", {"requests": 10})]])
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, "save_response", crash)
        async with open_workflow(planner, **settings(tmp_path, server=server)) as first:
            with pytest.raises(RuntimeError, match="Injected crash"):
                await first.run("模拟10个服务器请求", thread_id="crash")
    async with open_workflow(ScriptedPlanner([]), **settings(tmp_path, server=server)) as second:
        state = await second.resume(thread_id="crash")
    assert count == 1 and state["status"] == "completed"
    assert state["tool_calls"][0]["cached"] and state["customer_runs"] == 10


async def test_mcp_cannot_claim_supplied_samples_even_if_standalone_result_is_valid():
    config = ServerSimulationConfig(requests=3)
    result = run_server_simulation(config, generate_server_workload(config))
    assert result.workload_source == "supplied"
    with pytest.raises(ValueError, match="generated samples"):
        validate_tool_response("run_server_simulation", config.model_dump(mode="json"), result)


@pytest.mark.parametrize("kind", ["state_version", "schema_version", "scene", "mixed"])
async def test_checkpoint_incompatible_versions_or_scene_evidence_fail_closed(tmp_path, kind):
    async with open_workflow(
        ScriptedPlanner([[("run_server_simulation", {"requests": 3})]]), **settings(tmp_path)
    ) as workflow:
        state = await workflow.run("服务器实验", thread_id="versions")
    if kind in ("state_version", "schema_version"):
        state[kind] = "999"
    elif kind == "scene":
        state["scenario_id"] = "queue"
    else:
        state["tool_calls"].append({"name": "run_simulation"})
    with pytest.raises(ValueError):
        compatible_state(state)


async def test_server_tool_failure_preserves_retry_budget_and_real_recovery(tmp_path):
    from .test_workflow import error_server

    async with open_workflow(
        ScriptedPlanner([[("run_server_simulation", {"requests": 3})]]),
        **settings(tmp_path, server=error_server(retryable=True)),
    ) as workflow:
        state = await workflow.run("服务器实验", thread_id="retry")
    assert state["status"] == "completed" and state["retry_counts"]["tools"] == 1
    assert [call["status"] for call in state["tool_calls"]] == ["error", "success"]


async def test_server_json_contract_is_rejected_at_client_before_evidence():
    async def handler(ctx, params):
        response = await _call_tool(ctx, params)
        response.structured_content["unexpected"] = 1
        return response

    server = Server("bad-contract", on_list_tools=_list_tools, on_call_tool=handler)
    from sim2clip.agent.mcp_client import ToolFailure

    async with SimulationMCPClient(server) as client:
        with pytest.raises(ToolFailure) as error:
            await client.call("run_server_simulation", {"requests": 3})
    assert error.value.code == "INVALID_RESULT"


async def test_legacy_queue_mixed_single_and_comparison_batch_still_reports_comparison(tmp_path):
    from .test_workflow import compare, single

    planner = ScriptedPlanner([[compare([42, 43, 44, 45, 46]), single(seed=42)]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较收银台", thread_id="queue-batch")
    assert state["status"] == "completed" and state["scenario_id"] == "queue"
    assert len(state["tool_calls"]) == 2 and Path(state["artifacts"]["comparison"]).is_file()


@pytest.mark.parametrize("kind", ["missing", "content"])
async def test_completed_server_resume_rechecks_artifacts_without_recomputing(tmp_path, kind):
    planner = ScriptedPlanner([[("run_server_simulation", {"requests": 3})]])
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("服务器实验", thread_id="completed")
    path = Path(state["simulation_results"][0]["path"])
    if kind == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{}")
    unused = ScriptedPlanner([])
    async with open_workflow(unused, **settings(tmp_path)) as workflow:
        with pytest.raises((ValueError, OSError)):
            await workflow.resume(thread_id="completed")
    assert unused.states == []


async def test_queue_mcp_rejects_freshly_hashed_samples_from_a_different_seed():
    from sim2clip.agent.validation import contract_digest, validate_response
    from sim2clip.mcp_server.simulation_server import create_server
    from sim2clip.scenario_registry import tool_contract

    async with SimulationMCPClient(create_server()) as client:
        data = await client.call("run_simulation", {"cashiers": 1, "customers": 3, "seed": 42})
    data["config"]["seed"] = 43
    data["result_id"] = contract_digest(
        {key: data[key] for key in ("config", "workload_hash", "workload_source", "metadata")}
    )
    result = tool_contract("run_simulation").parse_result(data)
    # Old standalone validation remains valid for an otherwise consistent trace.
    validate_response("run_simulation", data["config"], result)
    with pytest.raises(ValueError, match="declared seed"):
        validate_tool_response("run_simulation", data["config"], result)
