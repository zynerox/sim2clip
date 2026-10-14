"""External deadlines keep SDK/AnyIO deadlocks diagnosable and fail the test."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "timeout",
        "native_task",
        "inner_anyio",
        "outer_anyio",
        "outer_asyncio",
        "exit",
        "discovery",
    ],
)
def test_stdio_cancellation_and_process_cleanup(tmp_path, case, record_property):
    log = tmp_path / "server.log"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).parent / "fixtures/cancellation_process.py"),
            case,
            str(log),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        stdout, stderr = process.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10
            )
        else:
            process.kill()
        stdout, stderr = process.communicate(timeout=10)
        pytest.fail(
            f"SDK cancellation hung in {case}; "
            f"server log={log.read_text() if log.exists() else ''}; "
            f"diagnostics={stderr.decode(errors='replace')}"
        )
    assert process.returncode == 0, stderr.decode(errors="replace")
    result = json.loads(stdout)
    record_property("resources", json.dumps(result))
    record_property("server_events", log.read_text(encoding="utf-8"))
    assert result["pending_async_tasks"] == result["live_child_processes"] == 0
    assert result["remaining_threads"] == []
