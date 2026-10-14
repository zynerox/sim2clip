import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from sim2clip.simulation import SimulationResult


def test_engine_imports_have_no_agent_or_mcp_dependency():
    root = Path(__file__).resolve().parents[2] / "src" / "sim2clip" / "simulation"
    forbidden = {"langgraph", "langchain", "langchain_core", "mcp", "openai", "anthropic"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(module.split(".")[0] in forbidden for module in modules), path.name


def test_json_contract_round_trips_in_a_fresh_process(tmp_path):
    output = tmp_path / "result.json"
    script = (
        "from pathlib import Path; "
        "from sim2clip.simulation import SimulationConfig, run_simulation; "
        "result = run_simulation(SimulationConfig(cashiers=3, customers=10, seed=42)); "
        f"Path({str(output)!r}).write_text(result.model_dump_json(), encoding='utf-8')"
    )
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = SimulationResult.model_validate(json.loads(output.read_text(encoding="utf-8")))
    assert result.metrics.completed_customers == 10
    assert len(result.events) == 30
