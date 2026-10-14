"""M1 arithmetic, independent reference poses and strict export dispatch."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from sim2clip.scenarios.server_contracts import RequestSample, ServerSimulationConfig
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.visualization.data import VisualizationError, parse_result
from sim2clip.visualization.pipeline import export_capabilities, export_demo
from sim2clip.visualization.server_replay import ServerEventReplay
from sim2clip.visualization.server_scene import ServerSceneTimeline


def hand_run(strategy="round_robin", servers=2):
    rows = ((0, 0, 3), (1, 1, 2), (2, 4, 1)) if servers == 1 else ((0, 0, 10), (1, 1, 1), (2, 3, 1))
    return run_server_simulation(
        ServerSimulationConfig(
            requests=3,
            routing_strategy=strategy,
            servers=tuple({"server_id": i + 1} for i in range(servers)),
        ),
        tuple(RequestSample(request_id=i, arrival_time=a, work=w) for i, a, w in rows),
    )


@pytest.mark.parametrize(
    "strategy,servers,time,queues,active,completed",
    [
        ("round_robin", 1, 1, {1: (1,)}, {1: 0}, 0),
        ("round_robin", 1, 3, {1: ()}, {1: 1}, 1),
        ("round_robin", 1, 4, {1: (2,)}, {1: 1}, 1),
        ("round_robin", 2, 3, {1: (2,), 2: ()}, {1: 0, 2: None}, 1),
        ("least_outstanding", 2, 3, {1: (), 2: ()}, {1: 0, 2: 2}, 1),
        ("least_outstanding", 2, 4, {1: (), 2: ()}, {1: 0, 2: None}, 2),
    ],
)
def test_m1_settled_states(strategy, servers, time, queues, active, completed):
    state = ServerEventReplay(hand_run(strategy, servers)).seek(time)
    assert state.queues == queues and state.active == active and state.completed == completed
    assert state.not_arrived + state.waiting + state.processing + state.completed == 3
    assert state.processing == sum(r is not None for r in active.values())


@pytest.mark.parametrize(
    "strategy,servers", [("round_robin", 1), ("round_robin", 2), ("least_outstanding", 2)]
)
def test_arbitrary_reverse_seeks_and_terminal_poses(strategy, servers):
    run = hand_run(strategy, servers)
    scene = ServerSceneTimeline(run)
    times = sorted(
        {e.timestamp for e in run.events}
        | {0.12, 1.12, 3.12, 4.12, run.metrics.simulation_end_time + 3}
    )
    for t in times + list(reversed(times)):
        pose = scene.seek(t)
        assert asdict(pose) == asdict(ServerSceneTimeline(run).seek(t))
        assert pose.not_arrived + pose.waiting + pose.processing + pose.completed == 3
        assert len({e.request_id for e in pose.entities}) == len(pose.entities)
        assert all(0 <= e.x <= 720 and 0 <= e.y <= pose.height for e in pose.entities)
    assert not scene.seek(run.metrics.simulation_end_time + 10).entities


@pytest.mark.parametrize("count", [1, 10, 1000, 10000])
def test_exact_aggregation_and_capacity(count):
    run = run_server_simulation(
        ServerSimulationConfig(
            requests=count,
            arrivals={"mode": "batch"},
            workload={"mode": "constant", "work": 1.0},
            servers=({"server_id": 3}, {"server_id": 8, "processing_rate": 2.0}),
        )
    )
    scene = ServerSceneTimeline(run)
    for t in (0, 0.1, 1, run.metrics.simulation_end_time):
        pose = scene.seek(t)
        assert pose.not_arrived + pose.waiting + pose.processing + pose.completed == count
        assert len(pose.entities) <= 17
        assert sum(s.queue_visible + s.queue_hidden for s in pose.servers) == pose.waiting
        assert sum(s.completed for s in pose.servers) == pose.completed
        assert sum(s.active_request is not None for s in pose.servers) == pose.processing
        assert all(
            s.outstanding == s.queue_count + int(s.active_request is not None) for s in pose.servers
        )


def test_same_time_completion_waiter_and_arrival():
    run = run_server_simulation(
        ServerSimulationConfig(requests=3),
        tuple(
            RequestSample(request_id=i, arrival_time=a, work=1.0) for i, a in enumerate((0, 0.5, 1))
        ),
    )
    s = ServerEventReplay(run).seek(1)
    assert s.active == {1: 1} and s.queues == {1: (2,)} and s.completed == 1 and s.event_cursor == 9


@pytest.mark.parametrize("t", [-1, float("nan"), float("inf")])
def test_illegal_clocks(t):
    run = hand_run()
    with pytest.raises(VisualizationError):
        ServerEventReplay(run).seek(t)
    with pytest.raises(VisualizationError):
        ServerSceneTimeline(run).seek(t)


@pytest.mark.parametrize("mutation", ["metric", "route", "sequence", "hash", "extra", "scenario"])
def test_corrupt_result_rejected(mutation):
    data = hand_run().model_dump(mode="json")
    if mutation == "metric":
        data["metrics"]["average_response_time"] += 1
    elif mutation == "route":
        data["request_records"][2]["server_id"] = 2
    elif mutation == "sequence":
        data["events"][0]["sequence"] = 1
    elif mutation == "hash":
        data["workload_hash"] = "a" * 64
    elif mutation == "extra":
        data["extra"] = 1
    else:
        data["scenario"] = "unsupported"
    with pytest.raises(VisualizationError):
        parse_result(data)


def test_server_export_is_html_only_and_preserves_existing_files(tmp_path: Path):
    run = hand_run()
    output = export_demo(run, tmp_path, report="# verified")
    assert set(output) == {"report", "result", "visualization", "timeline", "presentation"}
    manifest = json.loads(Path(output["presentation"]).read_text(encoding="utf-8"))
    assert manifest["presentation_version"] == "1"
    assert manifest["result_ref"]["result_id"] == run.result_id
    assert manifest["execution"]["evidence_completeness"] == "result_only"
    assert "server-replay" not in Path(output["visualization"]).read_text(
        encoding="utf-8"
    )  # scripts embedded
    assert export_capabilities(run)["gif"]["status"] == "unsupported"
    assert parse_result(run.model_dump(mode="json")) == run
    (tmp_path / "demo.gif").write_bytes(b"user file")
    with pytest.raises(ValueError, match="GIF"):
        export_demo(run, tmp_path)
    assert (tmp_path / "demo.gif").read_bytes() == b"user file"


def test_scene_rejects_mismatched_snapshot():
    scene = ServerSceneTimeline(hand_run())
    with pytest.raises(VisualizationError, match="clocks"):
        scene.seek(2, scene.replay.seek(1))


def test_recent_real_tail_routes_are_deducted_from_aggregation():
    run = run_server_simulation(
        ServerSimulationConfig(
            requests=30, arrivals={"mode": "batch"}, workload={"mode": "constant", "work": 1.0}
        )
    )
    scene = ServerSceneTimeline(run)
    during = scene.seek(0.12)
    assert {e.request_id for e in during.entities if e.moving} == {0, 1, 2, 3, 4, 5, 6, 27, 28, 29}
    assert (during.servers[0].queue_visible, during.servers[0].queue_hidden) == (9, 20)
    assert during.waiting == 29 and during.processing == 1
    assert all(e.server_id == 1 for e in during.entities)
    settled = scene.seek(0.35)
    assert (settled.servers[0].queue_visible, settled.servers[0].queue_hidden) == (6, 23)
    assert settled.waiting == 29 and settled.processing == 1


def test_unsupported_server_gif_is_distinct_from_queue_gif_failure(
    tmp_path, monkeypatch, hand_result
):
    def failed_gif(*args, **kwargs):
        raise RuntimeError("renderer failed")

    monkeypatch.setattr("sim2clip.visualization.pipeline.render_gif", failed_gif)
    assert "gif" not in export_demo(hand_run(), tmp_path / "server")
    with pytest.raises(VisualizationError, match="Queue GIF generation failed"):
        export_demo(hand_result, tmp_path / "queue")
    assert not (tmp_path / "queue" / "visualization.html").exists()


def test_refuse_replacing_different_saved_result(tmp_path):
    original = hand_run()
    export_demo(original, tmp_path)
    with pytest.raises(VisualizationError, match="different result"):
        export_demo(hand_run("least_outstanding"), tmp_path)
    assert parse_result(json.loads((tmp_path / "result.json").read_text())) == original


def test_cli_rejects_matching_identity_with_wrong_state_scenario(tmp_path, capsys):
    from sim2clip import cli

    run = hand_run()
    path = tmp_path / "result.json"
    path.write_bytes(run.model_dump_json().encode())
    state = tmp_path / "state.json"
    state.write_bytes(
        json.dumps(
            {"scenario_id": "queue", "simulation_results": [{"result_id": run.result_id}]}
        ).encode()
    )
    assert (
        cli.main(
            [
                "visualize",
                str(path),
                "--state",
                str(state),
                "--output",
                str(tmp_path / "bad"),
                "--json",
            ]
        )
        == 2
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["errors"][0]["code"] == "VISUALIZATION_ERROR"
    # R1.1 intentionally projects public errors through a fixed safe message.
    # Preserve the wrong-scenario rejection and ensure no internal field leaks.
    assert summary["errors"][0]["message"] == (
        "结果展示导出失败；请检查已保存的结果文件和输出目录。"
    )
    assert not (tmp_path / "bad").exists()
