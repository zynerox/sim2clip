"""Public evidence projection, traversal prevention and publication rollback."""

import json
from pathlib import Path

import pytest

from sim2clip.agent.artifacts import ArtifactStore
from sim2clip.scenarios.server_contracts import (
    ServerComparisonRequest,
    ServerConfig,
    ServerExperiment,
    ServerSimulationConfig,
)
from sim2clip.simulation.comparison import run_comparison
from sim2clip.simulation.queue_model import run_simulation
from sim2clip.simulation.schemas import SimulationConfig
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.visualization.data import VisualizationError
from sim2clip.visualization.html import render_html
from sim2clip.visualization.pipeline import export_demo, publish
from sim2clip.visualization.public import public_context, public_report, public_state
from sim2clip.visualization.timeline import execution_timeline


def test_public_exports_exclude_unknown_model_and_user_text(hand_result):
    secret = "sk-sensitive-canary-123"
    state = {
        "scenario_id": "queue",
        "run_id": "qa",
        "planner_mode": "llm",
        "user_query": secret,
        "experiment_goal": secret,
        "messages": [secret],
        "decision_history": [
            {"node": "planner", "round": 1, "action": "propose", "reason": secret}
        ],
        "tool_calls": [
            {
                "id": "r1-c0",
                "name": "run_simulation",
                "arguments": {"customers": 3, "api_key": secret},
                "provider_id": secret,
                "error": secret,
            }
        ],
    }
    timeline = execution_timeline([], state)
    report = public_report(hand_result, state)
    public = public_state(state, report)
    html = render_html(
        hand_result,
        timeline=[{**timeline[0], "reason": secret, "headers": {"Authorization": secret}}],
    )
    assert secret not in json.dumps(timeline) + json.dumps(public) + report + html
    assert secret in json.dumps(state)  # No mutation of the private audit history.


@pytest.mark.parametrize("value", ["../escape", "a/b", "a\\b", "E:/escape", ".."])
def test_artifact_ids_cannot_write_outside_root(tmp_path, value):
    store = ArtifactStore(tmp_path / "root")
    with pytest.raises(ValueError):
        store.save_response(value, {"id": "r1-c0", "name": "run_simulation", "arguments": {}}, {})
    with pytest.raises(ValueError):
        store.save_response("safe", {"id": value, "name": "run_simulation", "arguments": {}}, {})
    with pytest.raises(ValueError):
        store.write(tmp_path / "outside.json", b"bad")
    assert not (tmp_path / "outside.json").exists()


def test_staging_failure_leaves_no_success_bundle(tmp_path, hand_result, monkeypatch):
    from sim2clip.visualization import pipeline

    write = pipeline.atomic_write

    def fail(path, data):
        if path.name == "visualization.html":
            raise OSError("disk full canary")
        write(path, data)

    monkeypatch.setattr(pipeline, "atomic_write", fail)
    target = tmp_path / "new"
    with pytest.raises(OSError):
        export_demo(hand_result, target, frames=3)
    assert not target.exists()
    assert not list(tmp_path.glob(".sim2clip-export-*"))


def test_mid_publication_failure_restores_existing_files(tmp_path, monkeypatch):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "report.md").write_bytes(b"original")
    (target / "notes.txt").write_bytes(b"user data")
    replace = Path.replace

    def fail(path, destination):
        if path.name == "visualization.html":
            raise OSError("publication interrupted")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        publish(
            target,
            {
                "report": ("report.md", b"changed"),
                "result": ("result.json", b"{}"),
                "html": ("visualization.html", b"html"),
            },
        )
    assert (target / "report.md").read_bytes() == b"original"
    assert (target / "notes.txt").read_bytes() == b"user data"
    assert not (target / "result.json").exists()


def test_reserved_user_files_are_not_overwritten(tmp_path, hand_result):
    (tmp_path / "report.md").write_bytes(b"user document")
    with pytest.raises(VisualizationError, match="reserved"):
        export_demo(hand_result, tmp_path, frames=3)
    assert (tmp_path / "report.md").read_bytes() == b"user document"


@pytest.mark.parametrize("scene", ["queue", "server_load_balancing"])
def test_single_exports_describe_single_evidence_and_comparisons_remain_comparisons(scene):
    if scene == "queue":
        single = run_simulation(SimulationConfig(customers=20, cashiers=2))
        comparison = run_comparison(
            tuple(SimulationConfig(customers=20, cashiers=c) for c in (1, 3)), seeds=(42,)
        )
    else:
        config = ServerSimulationConfig(
            requests=20, servers=(ServerConfig(server_id=1), ServerConfig(server_id=2))
        )
        single = run_server_simulation(config)
        comparison = run_server_comparison(
            ServerComparisonRequest(
                comparison_axis="server_pool",
                experiments=(
                    ServerExperiment(
                        group_id="one",
                        config=config.model_copy(update={"servers": config.servers[:1]}),
                    ),
                    ServerExperiment(group_id="two", config=config),
                ),
                seeds=(42,),
            )
        )
    private = {"scenario_id": scene, "planner_mode": "llm", "experiment_goal": "PRIVATE_CANARY"}
    for result, expected in ((single, "描述单次"), (comparison, "比较")):
        context = public_context(private, result=result)
        report = public_report(result, private)
        html = render_html(result, context=context)
        assert context["experiment_goal"].startswith(expected)
        assert context["experiment_goal"] in report and context["experiment_goal"] in html
        assert "PRIVATE_CANARY" not in report + html
    single_state = {**private, "simulation_results": [{"result_id": "private_reference"}]}
    assert public_state(single_state)["experiment_goal"].startswith("描述单次")
    assert "配对实验共享顾客样本" not in public_report(single, private)
    assert "配对组共享到达时间和工作量" not in public_report(single, private)


@pytest.mark.parametrize("scene", [None, "unregistered_model", ["unregistered_model"]])
def test_unidentified_failed_request_is_not_presented_as_a_queue_comparison(scene):
    state = {
        "status": "failed",
        "scenario_id": scene,
        "user_query": "PRIVATE_CANARY",
        "experiment_goal": "PRIVATE_CANARY",
    }
    context = public_context(state)
    assert context["experiment_goal"] == "未生成已验证的实验结果"
    assert context["user_query"] == "实验请求尚未完成"
    assert "PRIVATE_CANARY" not in json.dumps(public_state(state))
