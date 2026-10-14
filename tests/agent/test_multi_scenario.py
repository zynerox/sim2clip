"""Mock native LLM messages, real model adapter, real MCP and unchanged graph."""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from sim2clip.agent import AgentPolicy, DemoPlanner, ModelPlanner, open_workflow
from sim2clip.scenario_registry import SCENARIOS, TOOLS

from .test_workflow import ScriptedPlanner, compare, settings


def server_arguments(seeds=(42, 43, 44, 45, 46), requests=30, deterministic=False):
    base = {"requests": requests, "seed": seeds[0], "servers": [{"server_id": 1}, {"server_id": 2}]}
    if deterministic:
        base.update(arrivals={"mode": "batch"}, workload={"mode": "constant", "work": 1})
    return {
        "comparison_axis": "routing_strategy",
        "seeds": list(seeds),
        "experiments": [
            {"group_id": "rr", "config": dict(base)},
            {"group_id": "lo", "config": {**base, "routing_strategy": "least_outstanding"}},
        ],
    }


def native_plan(name, arguments, goal="比较分流策略对平均响应时间的影响"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "id": "provider-call",
                "type": "tool_call",
                "args": {
                    "experiment_goal": goal,
                    "rationale": "在共享工作负载上做配对比较",
                    "assumptions": ["有限请求，采样分布按请求参数披露"],
                    "request": arguments,
                },
            }
        ],
    )


class NativeToolModel(BaseChatModel):
    responses: list[AIMessage]
    discovered: list[dict[str, Any]] = Field(default_factory=list)
    received: list[Any] = Field(default_factory=list)
    turns: int = 0

    @property
    def _llm_type(self):
        return "mock-native-tool-calling"

    def bind_tools(self, tools, **kwargs):
        self.discovered = tools
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(messages)
        answer = self.responses[min(self.turns, len(self.responses) - 1)].model_copy(deep=True)
        self.turns += 1
        return ChatResult(generations=[ChatGeneration(message=answer)])


@pytest.mark.anyio
@pytest.mark.parametrize("scene", ["queue", "server_load_balancing"])
async def test_official_questions_native_tool_selection_and_real_stdio_workflow(tmp_path, scene):
    if scene == "queue":
        query = "100 位顾客排队，比较 1 个和 3 个收银台。"
        name, args = compare([42, 43, 44, 45, 46], customers=100)
        answer = native_plan(name, args, "比较收银台数量对平均等待的影响")
    else:
        query = (
            "1000 个请求进入系统，比较 Round Robin 和 Least Outstanding "
            "哪种分流策略平均响应时间更低。"
        )
        name = "compare_server_simulations"
        answer = native_plan(name, server_arguments(requests=1000))
    model = NativeToolModel(responses=[answer])
    # Default server is an actual stdio child process, not in-process or mocked.
    async with open_workflow(
        ModelPlanner(model),
        checkpoint_path=tmp_path / "checkpoint.sqlite",
        artifact_root=tmp_path / "runs",
    ) as workflow:
        state = await workflow.run(query, thread_id=scene)
        nodes = set(workflow.graph.get_graph().nodes)
    assert state["status"] == "completed" and state["scenario_id"] == scene
    assert nodes == {
        "__start__",
        "planner",
        "parameter_validator",
        "executor",
        "result_validator",
        "report",
        "__end__",
    }
    assert state["validated_request"][0]["name"] == name
    assert state["tool_calls"][0]["provider_id"] == "provider-call"
    assert set(t["name"] for t in model.discovered) == set(TOOLS)
    assert isinstance(model.received[0][0], SystemMessage)
    assert query in [m.content for m in model.received[0]]
    assert "已注册场景" in model.received[0][0].content
    assert all(
        "events" not in r and "request_samples" not in r for r in state["simulation_results"]
    )
    assert len(state["simulation_results"]) == 10
    if scene == "server_load_balancing":
        assert state["comparison_result"]["winner_group_ids"]
        for phrase in (
            "平均响应",
            "P95",
            "平均等待",
            "队列峰值",
            "rr",
            "lo",
            "不是生产服务器性能预测",
        ):
            assert phrase in state["report"]
        assert {r["group_id"] for r in state["simulation_results"]} == {"rr", "lo"}
    else:
        assert "收银台" in state["report"]


@pytest.mark.anyio
async def test_wrong_scene_tool_selection_is_repaired_before_any_mcp_call(tmp_path):
    name, wrong = compare([42, 43, 44, 45, 46], customers=30)
    model = NativeToolModel(
        responses=[
            native_plan(name, wrong),
            native_plan("compare_server_simulations", server_arguments()),
        ]
    )
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run("比较30个请求的服务器分流策略", thread_id="wrong-scene")
    assert state["status"] == "completed"
    assert state["retry_counts"]["parameters"] == 1
    assert len(state["tool_calls"]) == 1
    assert state["tool_calls"][0]["name"] == "compare_server_simulations"
    replies = [m for m in model.received[1] if isinstance(m, ToolMessage)]
    assert replies[0].status == "error" and replies[0].tool_call_id == "r1-c0"
    assert "scenario" in replies[0].content
    assert state["experiment_goal"] == "比较分流策略对平均响应时间的影响"


@pytest.mark.anyio
async def test_server_pilot_expands_paired_seeds_with_native_tool_history(tmp_path):
    model = NativeToolModel(
        responses=[
            native_plan("compare_server_simulations", server_arguments((42,))),
            native_plan("compare_server_simulations", server_arguments()),
        ]
    )
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run("比较30个请求的服务器分流策略", thread_id="pilot")
    assert state["status"] == "completed" and len(state["tool_calls"]) == 2
    assert state["retry_counts"]["results"] == 1
    assert state["comparison_result"]["seeds"] == [42, 43, 44, 45, 46]
    assert len(state["simulation_results"]) == 10
    replies = [m for m in model.received[1] if isinstance(m, ToolMessage)]
    assert replies[0].status == "success"
    assert json.loads(replies[0].content)["seeds"] == [42]
    assert "events" not in json.loads(replies[0].content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query", ["模拟服务器故障对30个请求的影响", "模拟库存补货", "服务器超时重试实验"]
)
async def test_unsupported_scope_cannot_be_forced_into_a_registered_model(tmp_path, query):
    model = NativeToolModel(
        responses=[native_plan("compare_server_simulations", server_arguments())]
    )
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run(query, thread_id="unsupported")
    assert state["status"] == "failed" and not state["tool_calls"]
    assert state["feedback"]["code"] == "UNSUPPORTED_REQUEST"
    assert not state["report"]


@pytest.mark.anyio
async def test_native_model_refusal_is_explicit_without_fallback_tool(tmp_path):
    model = NativeToolModel(responses=[AIMessage(content="当前工具不支持故障实验")])
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run("模拟服务器故障", thread_id="refusal")
    assert state["status"] == "failed" and not state["tool_calls"]
    assert state["errors"][-1]["code"] == "UNSUPPORTED_REQUEST"


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["scene", "workload", "groups", "goal", "axis", "discard_seed"])
async def test_locked_design_rejects_winner_chasing_replans(tmp_path, change):
    first = native_plan("compare_server_simulations", server_arguments((42,)))
    name, args, goal = (
        "compare_server_simulations",
        server_arguments(),
        "比较分流策略对平均响应时间的影响",
    )
    if change == "scene":
        name, args = compare([42, 43, 44, 45, 46], customers=30)
    elif change == "workload":
        for e in args["experiments"]:
            e["config"]["workload"] = {"mode": "constant", "work": 1}
    elif change == "groups":
        args["experiments"][1]["group_id"] = "new_lo"
    elif change == "goal":
        goal = "换一个实验目标"
    elif change == "axis":
        args["comparison_axis"] = "server_pool"
        args["experiments"][1]["config"]["routing_strategy"] = "round_robin"
        args["experiments"][1]["config"]["servers"] = [{"server_id": 1}]
    else:
        args = server_arguments((43, 44, 45, 46, 47))
    model = NativeToolModel(responses=[first, native_plan(name, args, goal)])
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run("比较服务器分流", thread_id="lock")
    assert state["status"] == "failed" and len(state["tool_calls"]) == 1
    assert state["scenario_id"] == "server_load_balancing"
    assert state["design"]["seeds"] == [42]


@pytest.mark.anyio
async def test_deterministic_batch_constant_comparison_needs_one_seed(tmp_path):
    planner = ScriptedPlanner(
        [[("compare_server_simulations", server_arguments((42,), deterministic=True))]]
    )
    async with open_workflow(planner, **settings(tmp_path)) as workflow:
        state = await workflow.run("比较服务器策略", thread_id="deterministic")
    assert state["status"] == "completed" and state["retry_counts"]["results"] == 0


@pytest.mark.anyio
async def test_server_budget_counts_request_runs_before_execution(tmp_path):
    planner = ScriptedPlanner([[("compare_server_simulations", server_arguments())]])
    async with open_workflow(
        planner, **settings(tmp_path, policy=AgentPolicy(maximum_customer_runs=299))
    ) as workflow:
        state = await workflow.run("比较服务器分流", thread_id="budget")
    assert state["status"] == "failed" and not state["tool_calls"]


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["100个请求，比较1台服务器和3台服务器", "100顾客比较服务器策略"])
async def test_demo_planner_never_maps_server_questions_to_queue(tmp_path, query):
    async with open_workflow(DemoPlanner(), **settings(tmp_path)) as workflow:
        state = await workflow.run(query, thread_id="demo")
    assert state["status"] == "failed" and not state["tool_calls"]
    assert state["planner_mode"] == "offline_demo"


def test_registry_is_static_contract_metadata_and_lazy_adaptation_only():
    assert set(SCENARIOS) == {"queue", "server_load_balancing"}
    assert len(TOOLS) == 4
    with pytest.raises(TypeError):
        SCENARIOS["another"] = SCENARIOS["queue"]
    import ast

    source = Path(__file__).parents[2] / "src/sim2clip/scenario_registry.py"
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in (
                "sim2clip.simulation.queue_model",
                "sim2clip.simulation.server_model",
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad",
    [
        {"requests": True},
        {"requests": -1},
        {"requests": 0},
        {"unknown": 1},
        {"servers": [{"server_id": 1, "capacity": 2}]},
        {"routing_strategy": "random"},
    ],
)
async def test_native_server_parameters_are_repaired_before_mcp(tmp_path, bad):
    model = NativeToolModel(
        responses=[
            native_plan("run_server_simulation", {"requests": 3, **bad}),
            native_plan("run_server_simulation", {"requests": 3}),
        ]
    )
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run("模拟3个服务器请求", thread_id="bad-parameters")
    assert state["status"] == "completed" and state["retry_counts"]["parameters"] == 1
    assert len(state["tool_calls"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["capacity", "strategies", "population", "mixed"])
async def test_host_rejects_explicitly_contradictory_tool_designs(tmp_path, kind):
    if kind == "capacity":
        bad = native_plan("compare_server_simulations", server_arguments())
        query = "30个请求，比较1台和3台服务器"
        correct = server_arguments()
        correct["comparison_axis"] = "server_pool"
        correct["experiments"][0]["config"]["servers"] = [{"server_id": 1}]
        correct["experiments"][1]["config"]["servers"] = [{"server_id": i} for i in (1, 2, 3)]
        correct["experiments"][1]["config"]["routing_strategy"] = "round_robin"
    elif kind == "strategies":
        correct = server_arguments()
        query = "30个请求，比较Round Robin和Least Outstanding"
        wrong = server_arguments()
        wrong["comparison_axis"] = "server_pool"
        wrong["experiments"][1]["config"]["servers"] = [{"server_id": 1}]
        wrong["experiments"][1]["config"]["routing_strategy"] = "round_robin"
        bad = native_plan("compare_server_simulations", wrong)
    elif kind == "population":
        correct = server_arguments()
        query = "比较30个请求的服务器分流"
        bad = native_plan("compare_server_simulations", server_arguments(requests=10))
    else:
        correct = server_arguments()
        query = "比较服务器分流"
        bad = native_plan("compare_server_simulations", correct)
        other = native_plan("run_simulation", {"customers": 3})
        bad.tool_calls.extend(other.tool_calls)
    model = NativeToolModel(responses=[bad, native_plan("compare_server_simulations", correct)])
    async with open_workflow(ModelPlanner(model), **settings(tmp_path)) as workflow:
        state = await workflow.run(query, thread_id="conflict")
    assert state["status"] == "completed" and state["retry_counts"]["parameters"] == 1
    assert len(state["tool_calls"]) == 1


def test_explicitly_excluded_mechanisms_do_not_trigger_unsupported_scope():
    from sim2clip.agent.query_validation import check_query_scope

    for query in (
        "30个请求，不考虑故障、超时和网络延迟，比较服务器分流",
        "Compare server requests without retries and network latency",
    ):
        check_query_scope(query, "server_load_balancing")
