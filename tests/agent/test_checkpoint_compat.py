"""Cross-process/cross-version recovery of REAL pre-M3 SQLite checkpoints."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import sim2clip

FIXTURES = Path(__file__).parent / "fixtures"


def invoke(root, *args, legacy_source=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if legacy_source is not None:
        env["PYTHONPATH"] = str(legacy_source / "src")
    else:
        # Resume with the same package imported by this test, including wheel installs.
        env["PYTHONPATH"] = str(Path(sim2clip.__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, str(FIXTURES / "checkpoint_process.py"), "--root", str(root), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def checkpoints(path):
    with sqlite3.connect(path) as connection:
        return {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT checkpoint_id, checkpoint, metadata FROM checkpoints "
                "WHERE thread_id='legacy'"
            )
        }


@pytest.fixture(scope="module")
def actual_pre_m3_package(tmp_path_factory):
    archive = FIXTURES / "pre_m3_f16ed28.zip"
    # Pinned to the exact Git archive, checked independently of current source.
    expected = "f4f8323d1fd9944dfa6b0b03525c67a23bae959626868239e1e4c1d9b683b775"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    root = tmp_path_factory.mktemp("actual-pre-m3")
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (root / info.filename).resolve()
            assert target.is_relative_to(root.resolve())
        bundle.extractall(root)
    return root


@pytest.mark.parametrize(
    "boundary",
    ["planner", "parameter_validator", "executor", "result_validator", "report", "complete"],
)
def test_old_queue_checkpoint_resumes_in_new_process_without_rewriting_history(
    tmp_path, actual_pre_m3_package, boundary
):
    before = invoke(tmp_path, "--boundary", boundary, legacy_source=actual_pre_m3_package)
    assert str(actual_pre_m3_package) in before["package_source"]
    old = json.loads((tmp_path / "old-state.json").read_text(encoding="utf-8"))
    assert not any(key in old for key in ("scenario_id", "state_version", "schema_version"))
    original_rows = checkpoints(tmp_path / "checkpoints.sqlite")
    original_artifacts = {
        path: path.read_bytes() for path in (tmp_path / "runs").rglob("r*-c*-*.json")
    }
    after = invoke(tmp_path, "--resume")
    assert str(actual_pre_m3_package) not in after["package_source"]
    assert Path(after["package_source"]).resolve() == Path(sim2clip.__file__).resolve()
    assert after["status"] == "completed" and not after["next"]
    new = json.loads((tmp_path / "new-state.json").read_text(encoding="utf-8"))
    assert (new["scenario_id"], new["state_version"], new["schema_version"]) == ("queue", "2", "1")
    assert new["tool_calls"][: len(old["tool_calls"])] == old["tool_calls"]
    assert new["comparison_result"]["seeds"] == [42, 43, 44, 45, 46]
    current_ids = {
        identity
        for call in new["tool_calls"]
        if call["round"] == new["planning_round"] and call["status"] == "success"
        for identity in call["result_ids"]
    }
    assert len(new["simulation_results"]) == len(current_ids) == 10
    assert {run["result_id"] for run in new["simulation_results"]} == current_ids
    if boundary == "result_validator":
        # The old pilot and new comparison have distinct versioned IDs.
        assert {run["result_id"] for run in old["simulation_results"]}.isdisjoint(current_ids)
    for path, content in original_artifacts.items():
        assert path.read_bytes() == content
    assert "平均等待" in new["report"]
    final_rows = checkpoints(tmp_path / "checkpoints.sqlite")
    for identity, original in original_rows.items():
        assert final_rows[identity] == original
