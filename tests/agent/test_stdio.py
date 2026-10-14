"""Actual process restart plus stdio MCP; no engine or transport mocks."""

import json
import os
import subprocess
import sys
from pathlib import Path


def invoke(root: Path, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "examples/agent_workflow.py",
            "--output",
            str(root),
            "--thread-id",
            "restart",
            *options,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )


def test_agent_resume_in_a_new_process(tmp_path: Path) -> None:
    first = invoke(tmp_path, "--pause-after-tool")
    partial = json.loads(first.stdout.splitlines()[0])
    assert partial["tool_attempts"] == 1
    assert partial["next_nodes"] == ["result_validator"]
    saved = json.loads((tmp_path / "runs/restart/state.json").read_text(encoding="utf-8"))
    old_call = saved["tool_calls"][0]
    second = invoke(tmp_path, "--resume")
    summary = json.loads(second.stdout.splitlines()[0])
    assert summary["status"] == "completed"
    assert summary["tool_attempts"] == 2
    assert summary["planning_rounds"] == 2
    assert summary["planner_mode"] == "offline_demo"
    final = json.loads((tmp_path / "runs/restart/state.json").read_text(encoding="utf-8"))
    assert final["tool_calls"][0] == old_call
    assert final["comparison_result"]["seeds"] == [42, 43, 44, 45, 46]
    assert len(final["simulation_results"]) == 10
    assert final["retry_counts"]["results"] == 1
    assert (tmp_path / "checkpoints.sqlite").is_file()
