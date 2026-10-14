"""Provider free text stays in local audit evidence, never in public exports."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_provider_metadata_is_not_published_in_html_timeline_report_or_public_state(
    tmp_path, endpoint
):
    model = endpoint(["server_sensitive"])
    (tmp_path / ".env").write_text(
        f"MODEL_API_KEY=test-secret-not-real\nMODEL_BASE_URL={model.base_url}\nMODEL_NAME=fixture-model\n",
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "sim2clip",
            "run",
            "比较30个请求的服务器分流策略",
            "--thread-id",
            "public",
            "--output",
            str(tmp_path / "output"),
            "--json",
        ],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if not key.startswith("MODEL_")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    summary = json.loads(process.stdout)
    assert process.returncode == 0 and summary["status"] == "completed"
    assert "private-model-canary-123" not in process.stdout + process.stderr
    directory = Path(summary["output_directory"])
    assert Path(summary["artifacts"]["decisions"]) == directory / "decisions.json"
    for name in (
        "visualization.html",
        "agent-timeline.json",
        "report.md",
        "state.json",
        "decisions.json",
    ):
        text = (directory / name).read_text(encoding="utf-8")
        assert "private-model-canary-123" not in text
        assert "test-secret-not-real" not in text
    public = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    assert public["public_projection"] == "1" and "messages" not in public
    private = json.loads((tmp_path / "output/runs/public/state.json").read_text(encoding="utf-8"))
    assert "private-model-canary-123" in json.dumps(private)
