"""Local refusal diagnostics; model fixtures never connect to a remote API."""

import json

import pytest
from langchain_core.messages import AIMessage

from sim2clip.agent import ModelPlanner, open_workflow
from sim2clip.agent.public_errors import invalid_explicit_population, public_errors, task_output

from .test_multi_scenario import NativeToolModel, native_plan

CANARY = "SECRET_AUTHORIZATION_AND_HIDDEN_REASONING_CANARY"


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("模拟-5个请求，比较1台和3台服务器。", "INVALID_REQUEST_COUNT"),
        ("simulate 0 requests", "INVALID_REQUEST_COUNT"),
        ("模拟1.5个请求", "INVALID_REQUEST_COUNT"),
        ("模拟10001位顾客", "INVALID_CUSTOMER_COUNT"),
        ("模拟-2位顾客", "INVALID_CUSTOMER_COUNT"),
        ("模拟20个请求", None),
        ("用20位顾客模拟", None),
        ("请说明-5%的响应时间变化", None),
        ("例如-5个请求不合法，请模拟20个请求", None),
        ("模拟-5位顾客和20个请求", None),
        ("帮我模拟明天的天气变化", None),
        ("未识别的新问题", None),
    ],
)
def test_specific_reason_requires_unambiguous_input_and_schema_failure(query, code):
    assert invalid_explicit_population(query) == code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "code", "hint"),
    [
        ("帮我模拟明天的天气变化。", "UNSUPPORTED_REQUEST", "天气等未注册模型"),
        ("模拟-5个请求，比较1台和3台服务器。", "INVALID_REQUEST_COUNT", "请求数量必须是"),
        ("请处理未知问题", "UNSUPPORTED_REQUEST", "无法确认"),
    ],
)
async def test_model_no_tools_stops_once_without_copying_refusal_body(tmp_path, query, code, hint):
    model = NativeToolModel(
        responses=[AIMessage(content=CANARY, additional_kwargs={"reasoning_content": CANARY})]
    )
    async with open_workflow(
        ModelPlanner(model),
        checkpoint_path=tmp_path / "state.sqlite",
        artifact_root=tmp_path / "runs",
    ) as workflow:
        state = await workflow.run(query, thread_id="refusal")
    assert model.turns == 1
    assert state["status"] == "failed" and state["feedback"]["code"] == code
    assert not state["tool_calls"] and not state["simulation_results"] and not state["report"]
    assert len(state["errors"]) == 1  # No duplicated terminal PlannerFailure.
    errors = public_errors(state["errors"], feedback=state["feedback"])
    stdout, stderr = task_output(state["status"], state["report"], errors)
    assert stdout == "任务状态：failed\n"
    assert hint in stderr and len(stderr.splitlines()) == 1
    assert CANARY not in json.dumps(state) + stdout + stderr
    if code == "UNSUPPORTED_REQUEST":
        assert "无法确认" in stderr and "请求数量必须是" not in stderr


@pytest.mark.anyio
@pytest.mark.parametrize("planned_count", [-5, 20])
async def test_negative_user_population_cannot_execute_even_if_model_supplies_a_tool(
    tmp_path, planned_count
):
    model = NativeToolModel(
        responses=[
            native_plan(
                "run_server_simulation",
                {"requests": planned_count, "servers": [{"server_id": 1}, {"server_id": 2}]},
            )
        ]
    )
    async with open_workflow(
        ModelPlanner(model),
        checkpoint_path=tmp_path / "state.sqlite",
        artifact_root=tmp_path / "runs",
    ) as workflow:
        state = await workflow.run("模拟-5个请求", thread_id="illegal")
    assert state["status"] == "failed" and state["feedback"]["code"] == "INVALID_PARAMETERS"
    assert state["retry_counts"]["parameters"] == state["policy"]["parameter_replans"]
    assert not state["tool_calls"] and not state["simulation_results"]
    assert state["user_query"] == "模拟-5个请求"
    assert (
        public_errors(state["errors"], feedback=state["feedback"])[-1]["code"]
        == "INVALID_PARAMETERS"
    )


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_PLAN_JSON",
        "INVALID_PARAMETERS",
        "PLANNER_TIMEOUT",
        "ENGINE_ERROR",
        "UNKNOWN_VENDOR_CODE",
    ],
)
def test_public_errors_whitelist_codes_and_never_echo_private_diagnostics(code):
    raw = [{"code": code, "message": CANARY, "headers": {"Authorization": CANARY}}]
    clean = public_errors(raw)
    assert CANARY not in json.dumps(clean)
    assert clean[0]["code"] == ("WORKFLOW_ERROR" if code == "UNKNOWN_VENDOR_CODE" else code)
    assert CANARY in json.dumps(raw)  # Original local diagnostics are not mutated.


def test_legacy_duplicate_errors_use_structured_feedback_and_unknown_stays_generic():
    raw = [{"message": CANARY}, {"code": "UNSUPPORTED_REQUEST", "message": CANARY}]
    clean = public_errors(raw, feedback={"code": "UNSUPPORTED_REQUEST", "reason": CANARY})
    assert len(clean) == 1 and "无法确认" in clean[0]["message"]
    assert (
        public_errors([{"code": {"secret": CANARY}, "message": CANARY}])[0]["code"]
        == "WORKFLOW_ERROR"
    )
    assert (
        public_errors([{"message": CANARY}], feedback={"code": []})[0]["code"] == "WORKFLOW_ERROR"
    )


@pytest.mark.anyio
async def test_unexpected_planner_exception_is_not_misclassified_as_provider_auth_failure(tmp_path):
    from types import SimpleNamespace

    from sim2clip.agent.artifacts import ArtifactStore
    from sim2clip.agent.nodes import AgentNodes
    from sim2clip.agent.policy import AgentPolicy
    from sim2clip.agent.state import initial_state

    class BrokenPlanner:
        mode = "llm"

        async def plan(self, state, tools):
            raise RuntimeError(CANARY)

    nodes = AgentNodes(BrokenPlanner(), SimpleNamespace(tools=[]), ArtifactStore(tmp_path))
    update = await nodes.plan(initial_state("unknown", "failed", AgentPolicy().to_dict()))
    assert update["status"] == "failed"
    assert update["errors"][0]["code"] == "RUNTIME_ERROR"
    errors = public_errors(update["errors"])
    assert CANARY not in json.dumps(errors)
    assert "模型请求失败" not in errors[0]["message"]
