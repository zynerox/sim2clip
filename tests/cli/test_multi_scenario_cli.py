"""Mock provider HTTP, real SDK/native tool messages, stdio engines and process recovery."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

QUERY = "30个请求，比较 Round Robin 和 Least Outstanding 的平均响应时间"


def invoke(root, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MODEL_")}
    environment.update(PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1,localhost")
    result = subprocess.run(
        [sys.executable, "-m", "sim2clip", *arguments, "--output", str(root / "output"), "--json"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert "Traceback" not in result.stderr
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("pilot", [False, True])
def test_server_cli_recovers_in_a_new_process_with_paired_seed_supplement(
    tmp_path, endpoint, pilot
):
    model = endpoint(["server_pilot", "server"] if pilot else ["server"])
    credentials = tmp_path / ".env"
    credentials.write_text(
        f"MODEL_API_KEY=test-secret-not-real\nMODEL_BASE_URL={model.base_url}\nMODEL_NAME=fixture-model\n",
        encoding="utf-8",
    )
    process, first = invoke(tmp_path, "run", QUERY, "--thread-id", "servers", "--pause-after-tool")
    assert process.returncode == 0 and first["status"] == "paused"
    assert first["next_nodes"] == ["result_validator"]
    before = json.loads(Path(first["artifacts"]["state"]).read_text(encoding="utf-8"))
    assert before["scenario_id"] == "server_load_balancing" and len(before["tool_calls"]) == 1
    if not pilot:
        # Resume the validated computation without a credential or another LLM turn.
        credentials.unlink()
    process, final = invoke(tmp_path, "resume", "servers")
    assert process.returncode == 0 and final["status"] == "completed", final
    saved = json.loads(Path(final["artifacts"]["state"]).read_text(encoding="utf-8"))
    assert saved["tool_calls"][0] == before["tool_calls"][0]
    assert saved["comparison_result"]["seeds"] == [42, 43, 44, 45, 46]
    assert saved["retry_counts"]["results"] == int(pilot)
    assert len(model.requests) == 1 + int(pilot)
    assert saved["state_version"] == "2" and saved["schema_version"] == "1"
    assert final["visualization"]["supported"] is True
    assert final["visualization"]["gif"]["status"] == "unsupported"
    assert "gif" not in final["artifacts"]
    assert Path(final["artifacts"]["visualization"]).is_file()
    assert Path(final["artifacts"]["comparison"]).is_file()
    assert "不是生产服务器性能预测" in saved["report"]
    assert "test-secret-not-real" not in process.stdout + process.stderr + json.dumps(saved)
    if pilot:
        second_messages = model.requests[1]["body"]["messages"]
        tool_messages = [message for message in second_messages if message["role"] == "tool"]
        assert len(tool_messages) == 1 and tool_messages[0]["tool_call_id"] == "r1-c0"
        assert "INSUFFICIENT_EVIDENCE" in second_messages[0]["content"]
    again, resumed = invoke(tmp_path, "resume", "servers")
    assert again.returncode == 0 and resumed["tool_attempts"] == final["tool_attempts"]
    assert len(model.requests) == 1 + int(pilot)
    visualization = subprocess.run(
        [
            sys.executable,
            "-m",
            "sim2clip",
            "visualize",
            final["artifacts"]["comparison"],
            "--output",
            str(tmp_path / "visualization"),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert visualization.returncode == 0
    exported = json.loads(visualization.stdout)
    assert exported["visualization"]["gif"]["status"] == "unsupported"
    assert Path(exported["artifacts"]["visualization"]).is_file()
    assert "gif" not in exported["artifacts"]


def test_server_question_in_offline_cli_is_explicitly_rejected(tmp_path):
    result, summary = invoke(
        tmp_path, "run", "100个请求，比较1台服务器和3台服务器", "--planner", "demo"
    )
    assert result.returncode == 1 and summary["tool_attempts"] == 0
    assert (
        summary["planner"] == "demo"
        and "Offline demo supports only" in summary["errors"][0]["message"]
    )
