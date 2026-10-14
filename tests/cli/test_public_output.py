"""Real CLI/SDK/stdio using localhost fixtures, never a remote model."""

import json
import os
import subprocess
import sys

import pytest

from sim2clip import cli

CANARIES = ("private-model-canary-123", "hidden-reasoning-canary-456", "test-secret-not-real")


def invoke(tmp_path, model, query, *, as_json=False):
    env_file = tmp_path / "fixture.env"
    env_file.write_text(
        f"MODEL_API_KEY=test-secret-not-real\nMODEL_BASE_URL={model.base_url}\nMODEL_NAME=fixture-model\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_")}
    env.update(PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1,localhost")
    args = [
        sys.executable,
        "-m",
        "sim2clip",
        "run",
        query,
        "--env-file",
        str(env_file),
        "--output",
        str(tmp_path / "output"),
        "--thread-id",
        "public-test",
        "--skip-visualization",
    ]
    if as_json:
        args.append("--json")
    return subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60
    )


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    ("query", "code", "hint"),
    [
        ("帮我模拟明天的天气变化。", "UNSUPPORTED_REQUEST", "天气等未注册模型"),
        ("模拟-5个请求，比较1台和3台服务器。", "INVALID_REQUEST_COUNT", "请求数量必须是"),
        ("未知的实验问题", "UNSUPPORTED_REQUEST", "无法确认"),
    ],
)
def test_user_visible_refusal_and_exit_code(tmp_path, endpoint, as_json, query, code, hint):
    model = endpoint(["text_only_sensitive"])
    process = invoke(tmp_path, model, query, as_json=as_json)
    assert process.returncode == 1 and len(model.requests) == 1
    combined = process.stdout + process.stderr
    assert all(canary not in combined for canary in CANARIES)
    assert "Traceback" not in combined and hint in combined
    if as_json:
        summary = json.loads(process.stdout)
        assert summary["status"] == "failed" and summary["tool_attempts"] == 0
        assert summary["scenario_id"] is None and summary["experiment_goal"] is None
        assert [error["code"] for error in summary["errors"]] == [code]
    else:
        assert "任务状态：failed" in process.stdout
        assert process.stderr.count(code) == 1
        assert "实验报告" not in process.stdout


@pytest.mark.parametrize("scene", ["queue", "server"])
def test_single_success_report_uses_verified_public_data_even_without_visualization(
    tmp_path, endpoint, scene
):
    query = (
        "用2个收银台模拟20位顾客排队。"
        if scene == "queue"
        else "用两台服务器和Round Robin策略模拟20个请求。"
    )
    model = endpoint([f"{scene}_single_sensitive"])
    process = invoke(tmp_path, model, query)
    assert process.returncode == 0 and len(model.requests) == 1
    combined = process.stdout + process.stderr
    assert all(canary not in combined for canary in CANARIES)
    assert "任务状态：completed" in process.stdout and "实验目标：描述单次" in process.stdout
    assert "实验目标：比较" not in combined and "最低的组：" not in combined
    assert "comparison_id：" not in combined and "最短的配置为" not in combined
    if scene == "queue":
        assert "| 2 | 20 |" in process.stdout and "指数到达间隔均值 10.0 秒" in process.stdout
    else:
        assert "请求 20；策略 round_robin" in process.stdout
        assert "P95 使用 nearest-rank" in process.stdout and "工作量假设" in process.stdout


@pytest.mark.parametrize("as_json", [False, True])
def test_internal_exception_is_not_user_visible(tmp_path, monkeypatch, capsys, as_json):
    async def fail(args):
        raise RuntimeError("test-secret-not-real hidden-reasoning-canary-456")

    monkeypatch.setattr(cli, "execute", fail)
    argv = ["run", "unknown", "--output", str(tmp_path)] + (["--json"] if as_json else [])
    assert cli.main(argv) == 1
    output = capsys.readouterr()
    assert all(canary not in output.out + output.err for canary in CANARIES)
    assert "RuntimeError" not in output.out + output.err
    assert "RUNTIME_ERROR" in output.out + output.err
