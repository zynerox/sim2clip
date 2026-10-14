"""Installed CLI + real provider SDK HTTP + real stdio MCP subprocess."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sim2clip import cli

QUERY = "帮我模拟午高峰12个顾客，比较2个收银台和4个收银台的等待时间"
DEMO_QUERY = "100个顾客，比较1个收银台和3个收银台"


def model_env(tmp_path: Path, endpoint: Any) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"MODEL_API_KEY=test-secret-not-real\nMODEL_BASE_URL={endpoint.base_url}\nMODEL_NAME=fixture-model\n",
        encoding="utf-8",
    )
    return path


def invoke(
    tmp_path: Path, *arguments: str, installed: bool = False
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    command = (
        [str(Path(sys.executable).with_name("sim2clip.exe"))]
        if installed
        else [sys.executable, "-m", "sim2clip"]
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("MODEL_")}
    env.update(PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1,localhost")
    completed = subprocess.run(
        [
            *command,
            *arguments,
            "--output",
            str(tmp_path / "output"),
            "--json",
            "--skip-visualization",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert "Traceback" not in completed.stderr
    return completed, json.loads(completed.stdout)


def state(summary: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(summary["artifacts"]["state"]).read_text(encoding="utf-8"))


def test_natural_query_with_real_sdk_and_default_dotenv(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["normal"])
    model_env(tmp_path, server)
    completed, summary = invoke(
        tmp_path, "run", QUERY, "--thread-id", "natural", installed=sys.platform == "win32"
    )
    assert completed.returncode == 0 and summary["status"] == "completed"
    assert summary["planner"] == "model"
    assert summary["experiment_goal"] != QUERY
    saved = state(summary)
    assert saved["user_query"] == QUERY
    assert saved["validated_request"][0]["arguments"]["experiments"][0]["customers"] == 12
    assert {r["config"]["cashiers"] for r in saved["simulation_results"]} == {2, 4}
    assert len(saved["simulation_results"]) == 10
    assert server.requests[0]["path"] == "/v1/chat/completions"
    body = server.requests[0]["body"]
    assert body["model"] == "fixture-model"
    assert QUERY in [message["content"] for message in body["messages"]]
    assert {tool["function"]["name"] for tool in body["tools"]} == {
        "run_simulation",
        "compare_simulations",
        "run_server_simulation",
        "compare_server_simulations",
    }
    assert server.headers == ["Bearer test-secret-not-real"]
    assert "test-secret-not-real" not in completed.stdout + completed.stderr
    assert "test-secret-not-real" not in Path(summary["artifacts"]["state"]).read_text(
        encoding="utf-8"
    )
    assert Path(summary["artifacts"]["comparison"]).is_file()


def test_invalid_json_replans_and_recovers(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["invalid_json", "normal"])
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 0
    assert summary["planning_rounds"] == 2 and summary["tool_attempts"] == 1
    assert summary["retry_counts"]["parameters"] == 1
    assert summary["errors"][0]["code"] == "INVALID_PLAN_JSON"
    assert "INVALID_PLAN_JSON" in server.requests[1]["body"]["messages"][0]["content"]


def test_invalid_model_parameters_replan_before_mcp(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["bad_parameters", "normal"])
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 0
    assert summary["planning_rounds"] == 2 and summary["tool_attempts"] == 1
    assert summary["retry_counts"]["parameters"] == 1
    assert "INVALID_PARAMETERS" in server.requests[1]["body"]["messages"][0]["content"]


def test_explicit_env_file_and_english_question(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["normal"])
    path = model_env(tmp_path, server)
    renamed = tmp_path / "provider.env"
    path.rename(renamed)
    query = "Compare 2 and 4 cashiers serving 12 customers during the lunch rush"
    completed, summary = invoke(tmp_path, "run", query, "--env-file", str(renamed))
    assert completed.returncode == 0 and summary["status"] == "completed"
    assert state(summary)["user_query"] == query


def test_completed_resume_and_duplicate_task_id(tmp_path: Path) -> None:
    _, original = invoke(
        tmp_path, "run", DEMO_QUERY, "--planner", "demo", "--thread-id", "completed"
    )
    completed, resumed = invoke(tmp_path, "resume", "completed")
    assert completed.returncode == 0 and resumed["tool_attempts"] == original["tool_attempts"]
    duplicate, error = invoke(
        tmp_path, "run", DEMO_QUERY, "--planner", "demo", "--thread-id", "completed"
    )
    assert duplicate.returncode == 2
    assert error["errors"][0]["code"] == "CONFIGURATION_ERROR"
    assert "已有任务请使用 resume" in error["errors"][0]["message"]


def test_invalid_json_exhausts_finite_budget_without_mcp_call(
    tmp_path: Path, endpoint: Any
) -> None:
    server = endpoint(["invalid_json"])
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 1 and summary["status"] == "failed"
    assert len(server.requests) == 3 and summary["tool_attempts"] == 0
    assert summary["errors"][-1]["code"] == "INVALID_PLAN_JSON"
    assert state(summary)["simulation_results"] == []


@pytest.mark.parametrize("response", ["extra_results", "text_only", "unknown_tool"])
def test_model_cannot_supply_results_or_unknown_tools(
    tmp_path: Path, endpoint: Any, response: str
) -> None:
    server = endpoint([response])
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 1
    assert summary["tool_attempts"] == 0
    assert not state(summary)["report"]


def test_planner_timeout_has_machine_readable_failure(tmp_path: Path, endpoint: Any) -> None:
    # Allow first-request SDK setup, then time out while the server delays its response.
    server = endpoint(["timeout"], timeout_delay=3.0)
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY, "--planner-timeout", "1.0")
    assert completed.returncode == 1 and summary["status"] == "failed"
    assert summary["errors"][-1]["code"] == "PLANNER_TIMEOUT"
    assert len(server.requests) == 1 and summary["tool_attempts"] == 0
    assert Path(summary["checkpoint"]).exists()


def test_provider_error_body_and_key_are_not_exported(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["http_error"])
    model_env(tmp_path, server)
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 1
    assert summary["errors"][-1]["code"] == "PLANNER_REQUEST_ERROR"
    assert len(server.requests) == 1  # provider SDK retries are disabled
    assert "test-secret-not-real" not in completed.stdout + completed.stderr
    assert "test-secret-not-real" not in json.dumps(state(summary))


def test_model_resume_after_tools_needs_no_new_model_call_or_credentials(
    tmp_path: Path, endpoint: Any
) -> None:
    server = endpoint(["normal"])
    dotenv = model_env(tmp_path, server)
    _, partial = invoke(tmp_path, "run", QUERY, "--thread-id", "model-resume", "--pause-after-tool")
    assert partial["status"] == "paused" and partial["next_nodes"] == ["result_validator"]
    old_call = state(partial)["tool_calls"][0]
    dotenv.unlink()
    completed, final = invoke(tmp_path, "resume", "model-resume")
    assert completed.returncode == 0 and final["status"] == "completed"
    assert final["planner"] == "model" and len(server.requests) == 1
    assert final["tool_attempts"] == 1
    assert state(final)["tool_calls"][0] == old_call


def test_model_multiround_results_and_checkpoint_resume(tmp_path: Path, endpoint: Any) -> None:
    server = endpoint(["pilot", "normal"])
    model_env(tmp_path, server)
    _, partial = invoke(tmp_path, "run", QUERY, "--thread-id", "pilot-resume", "--pause-after-tool")
    assert partial["tool_attempts"] == 1
    completed, final = invoke(tmp_path, "resume", "pilot-resume")
    assert completed.returncode == 0 and final["tool_attempts"] == 2
    assert final["retry_counts"]["results"] == 1
    body = server.requests[1]["body"]
    assert any(message["role"] == "tool" for message in body["messages"])
    assert "INSUFFICIENT_EVIDENCE" in body["messages"][0]["content"]


def test_resume_checkpoint_created_before_first_planner(tmp_path: Path, endpoint: Any) -> None:
    import asyncio

    from sim2clip.agent import open_workflow

    class UncalledPlanner:
        mode = "llm"

        async def plan(self, state: Any, tools: Any) -> Any:
            raise AssertionError("Paused before planner")

    server = endpoint(["normal"])
    model_env(tmp_path, server)

    async def pause() -> None:
        async with open_workflow(
            UncalledPlanner(),
            checkpoint_path=tmp_path / "output/checkpoints.sqlite",
            artifact_root=tmp_path / "output/runs",
            interrupt_before=["planner"],
        ) as workflow:
            initial = await workflow.run(QUERY, thread_id="before-planner")
            assert initial["planner_mode"] == "llm"
            assert initial["planning_round"] == 0

    asyncio.run(pause())
    completed, summary = invoke(tmp_path, "resume", "before-planner")
    assert completed.returncode == 0 and summary["status"] == "completed"
    assert summary["planning_rounds"] == 1 and len(server.requests) == 1


def test_json_interrupt_reports_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    async def interrupted(args: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "execute", interrupted)
    code = cli.main(["run", DEMO_QUERY, "--planner", "demo", "--output", str(tmp_path), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 130 and summary["status"] == "interrupted"
    assert summary["thread_id"].startswith("run-")


def test_demo_runs_offline_and_preserves_original_planner_on_resume(tmp_path: Path) -> None:
    _, partial = invoke(
        tmp_path,
        "run",
        DEMO_QUERY,
        "--planner",
        "demo",
        "--thread-id",
        "demo",
        "--pause-after-tool",
    )
    assert partial["status"] == "paused"
    completed, summary = invoke(tmp_path, "resume", "demo")
    assert completed.returncode == 0 and summary["planner"] == "demo"
    assert summary["tool_attempts"] == 2
    refused, failure = invoke(tmp_path, "resume", "demo", "--planner", "model")
    assert refused.returncode == 2 and failure["status"] == "failed"


def test_missing_config_and_unknown_checkpoint_are_clear_errors(tmp_path: Path) -> None:
    completed, summary = invoke(tmp_path, "run", QUERY)
    assert completed.returncode == 2
    assert summary["errors"][0]["code"] == "CONFIGURATION_ERROR"
    assert "MODEL_API_KEY" in summary["errors"][0]["message"]
    completed, summary = invoke(tmp_path, "resume", "missing")
    assert completed.returncode == 2


def test_cli_tool_failure_is_a_failed_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from functools import partial

    from mcp.server import Server
    from mcp.types import CallToolResult, TextContent

    from sim2clip.mcp_server.simulation_server import _list_tools

    async def failed_tool(ctx: Any, params: Any) -> CallToolResult:
        payload = {
            "error": {
                "code": "ENGINE_ERROR",
                "message": "Injected tool failure",
                "retryable": False,
            }
        }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
            is_error=True,
        )

    server = Server("cli-error-fixture", on_list_tools=_list_tools, on_call_tool=failed_tool)
    monkeypatch.setattr(cli, "open_workflow", partial(cli.open_workflow, server=server))
    code = cli.main(["run", DEMO_QUERY, "--planner", "demo", "--output", str(tmp_path), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 1 and summary["status"] == "failed"
    assert summary["tool_attempts"] == 1
    assert "ENGINE_ERROR" in summary["errors"][-1]["message"]
    assert not state(summary)["simulation_results"]
