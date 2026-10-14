import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sim2clip.agent.server_validation import validate_server_comparison, validate_server_simulation
from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult

EXAMPLE = Path(__file__).parents[2] / "examples/server_load_balancing.py"


def execute(*args, hash_seed="1"):
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    return subprocess.run(
        [sys.executable, str(EXAMPLE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
        check=False,
    )


@pytest.mark.parametrize("experiment", ["single", "capacity", "strategy"])
def test_standalone_json_is_identical_across_processes_and_hash_seeds(experiment):
    args = ("--experiment", experiment, "--requests", "20", "--seeds", "42", "--rates", "1,2,0.5")
    first = execute(*args, hash_seed="1")
    second = execute(*args, hash_seed="783")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout == second.stdout
    if experiment == "single":
        result = ServerSimulationResult.model_validate_json(first.stdout)
        validate_server_simulation(result)
    else:
        result = ServerComparisonResult.model_validate_json(first.stdout)
        validate_server_comparison(result)


def test_standalone_exports_and_refuses_to_overwrite_existing_file(tmp_path):
    path = tmp_path / "new" / "result.json"
    result = execute("--requests", "4", "--seeds", "42,43", "--output", str(path))
    assert result.returncode == 0, result.stderr
    original = path.read_bytes()
    validate_server_comparison(ServerComparisonResult.model_validate_json(original))
    failed = execute("--requests", "4", "--seeds", "42,43", "--output", str(path))
    assert failed.returncode != 0
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "args",
    [
        ("--experiment", "single", "--seeds", "42,43"),
        ("--experiment", "capacity", "--rates", "1,2"),
        ("--experiment", "strategy", "--strategy", "least_outstanding"),
        ("--arrival-mode", "batch", "--mean-arrival", "2"),
    ],
)
def test_standalone_rejects_ambiguous_or_unused_options(args):
    assert execute(*args).returncode != 0


def test_engine_runs_even_if_queue_engine_functions_are_disabled(monkeypatch):
    from sim2clip.scenarios.server_contracts import ServerSimulationConfig
    from sim2clip.simulation import queue_model
    from sim2clip.simulation.server_model import run_server_simulation

    def forbidden(*args, **kwargs):
        raise AssertionError("Server model called convenience-store engine")

    monkeypatch.setattr(queue_model, "run_simulation", forbidden)
    result = run_server_simulation(ServerSimulationConfig(requests=10))
    validate_server_simulation(result)


def test_server_modules_and_contracts_have_no_queue_model_dependency():
    root = Path(__file__).parents[2] / "src/sim2clip"
    paths = list((root / "simulation").glob("server_*.py")) + list(
        (root / "scenarios").glob("*.py")
    )
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any("queue_model" in m or m.endswith(".simulation.schemas") for m in modules)
