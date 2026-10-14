"""Installed CLI exports all four deliverables through real MCP + checkpoint history."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sim2clip import cli
from sim2clip.visualization.data import load_result


def invoke(tmp_path: Path, *arguments: str) -> tuple[int, dict[str, Any]]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_")}
    completed = subprocess.run(
        [sys.executable, "-m", "sim2clip", *arguments, "--json"],
        env=env,
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=90,
    )
    assert "Traceback" not in completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def test_run_pipeline_resume_and_export_from_saved_result(tmp_path: Path) -> None:
    code, summary = invoke(
        tmp_path,
        "run",
        "100个顾客，比较1个收银台和3个收银台",
        "--planner",
        "demo",
        "--thread-id",
        "lunch",
    )
    assert code == 0 and summary["status"] == "completed"
    directory = tmp_path / "outputs/lunch"
    assert {"report.md", "result.json", "visualization.html", "demo.gif"}.issubset(
        p.name for p in directory.iterdir()
    )
    result = load_result(directory / "result.json")
    assert len(result.results) == 10  # type: ignore[union-attr]
    timeline = json.loads((directory / "agent-timeline.json").read_text(encoding="utf-8"))
    assert {"planning", "tool", "validation", "report"}.issubset(
        row["category"] for row in timeline
    )
    assert len([row for row in timeline if row["category"] == "tool"]) == 2
    assert any(row["status"] == "retry" for row in timeline)
    assert all(row["timestamp"] for row in timeline)
    before = hashlib.sha256((directory / "demo.gif").read_bytes()).hexdigest()
    _, resume = invoke(tmp_path, "resume", "lunch")
    assert resume["tool_attempts"] == 2
    assert hashlib.sha256((directory / "demo.gif").read_bytes()).hexdigest() == before
    code, rerender = invoke(
        tmp_path,
        "visualize",
        str(directory / "result.json"),
        "--state",
        str(directory / "state.json"),
        "--checkpoint",
        str(tmp_path / "outputs/checkpoints.sqlite"),
        "--output",
        str(tmp_path / "rerender"),
    )
    assert code == 0 and rerender["status"] == "completed"
    assert (
        json.loads(Path(rerender["artifacts"]["timeline"]).read_text(encoding="utf-8")) == timeline
    )
    assert load_result(Path(rerender["artifacts"]["result"])) == result


def test_paused_run_defers_visualization_until_resumed(tmp_path: Path) -> None:
    code, partial = invoke(
        tmp_path,
        "run",
        "100个顾客，比较1个收银台和3个收银台",
        "--planner",
        "demo",
        "--thread-id",
        "paused",
        "--pause-after-tool",
    )
    assert code == 0 and partial["status"] == "paused"
    assert "gif" not in partial["artifacts"] and not (tmp_path / "outputs/paused/demo.gif").exists()
    code, final = invoke(tmp_path, "resume", "paused")
    assert code == 0 and Path(final["artifacts"]["gif"]).is_file()


def test_cli_missing_events_has_clear_error(tmp_path: Path, demo_files: dict[str, str]) -> None:
    value = json.loads(Path(demo_files["result"]).read_text(encoding="utf-8"))
    value["results"][0]["events"].pop()
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(value))
    code, summary = invoke(tmp_path, "visualize", str(path), "--output", str(tmp_path / "bad"))
    assert code == 2 and summary["errors"][0]["code"] == "VISUALIZATION_ERROR"
    assert not (tmp_path / "bad/demo.gif").exists()


def test_visualize_interrupt_has_valid_json_without_task_id(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    async def interrupted(args: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "execute", interrupted)
    code = cli.main(["visualize", "result.json", "--output", str(tmp_path), "--json"])
    assert code == 130 and json.loads(capsys.readouterr().out) == {"status": "interrupted"}


@pytest.mark.parametrize("state_content", ["not json", "[]", '{"simulation_results": []}'])
def test_invalid_or_unrelated_agent_state_is_rejected(
    tmp_path: Path, demo_files: dict[str, str], state_content: str
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(state_content, encoding="utf-8")
    code, summary = invoke(
        tmp_path,
        "visualize",
        demo_files["result"],
        "--state",
        str(state_path),
        "--output",
        str(tmp_path / "bad-state"),
    )
    assert code == 2 and summary["errors"][0]["code"] == "VISUALIZATION_ERROR"
    assert not (tmp_path / "bad-state/demo.gif").exists()
