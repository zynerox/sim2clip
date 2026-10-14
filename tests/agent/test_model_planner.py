"""Exercise the real LangChain model adapter without a paid network request."""

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from sim2clip.agent import ModelPlanner, open_workflow
from sim2clip.mcp_server.simulation_server import create_server


class ToolCallingModel(BaseChatModel):
    discovered: list[dict[str, Any]] = Field(default_factory=list)
    received_tool_results: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "test-tool-calling-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        self.discovered = tools
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        results = [message for message in messages if isinstance(message, ToolMessage)]
        self.received_tool_results = results
        answer = AIMessage(
            content="Compare the cashier configurations",
            tool_calls=[
                {
                    "name": "compare_simulations",
                    "args": {
                        "experiment_goal": "比较收银台数量对平均等待时间的影响",
                        "rationale": "用配对种子保持需求相同",
                        "assumptions": ["指数到达和服务时长"],
                        "request": {
                            "experiments": [{"cashiers": n, "customers": 10} for n in (1, 3)],
                            "seeds": [42] if not results else [42, 43, 44, 45, 46],
                        },
                    },
                    "id": "model-call",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=answer)])


@pytest.mark.anyio
async def test_model_receives_mcp_schemas_and_prior_tool_evidence(tmp_path: Any) -> None:
    model = ToolCallingModel()
    async with open_workflow(
        ModelPlanner(model),
        checkpoint_path=tmp_path / "state.sqlite",
        artifact_root=tmp_path / "artifacts",
        server=create_server(),
    ) as workflow:
        state = await workflow.run("比较收银台", thread_id="model-adapter")
    assert state["status"] == "completed"
    assert state["planner_mode"] == "llm"
    assert len(state["tool_calls"]) == 2
    assert {tool["name"] for tool in model.discovered} == {
        "run_simulation",
        "compare_simulations",
        "run_server_simulation",
        "compare_server_simulations",
    }
    comparison_schema = next(
        t["parameters"] for t in model.discovered if t["name"] == "compare_simulations"
    )
    assert comparison_schema["additionalProperties"] is False
    assert comparison_schema["properties"]["request"]["additionalProperties"] is False
    assert state["experiment_goal"] == "比较收银台数量对平均等待时间的影响"
    assert model.received_tool_results[0].tool_call_id == "r1-c0"
    data = json.loads(model.received_tool_results[0].content)
    assert data["seeds"] == [42] and data["summaries"]
    assert "events" not in data  # model context contains evidence summaries, not all replay data
