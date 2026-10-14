"""Actual offline Chromium, Python/JS vectors, UI controls and scaling."""

from dataclasses import asdict

import pytest

from sim2clip.scenarios.server_contracts import ServerComparisonRequest, ServerSimulationConfig
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.visualization.html import render_html
from sim2clip.visualization.server_replay import ServerEventReplay
from sim2clip.visualization.server_scene import ServerSceneTimeline
from tests.visualization.test_server_replay import hand_run


def comparison(count=10, seeds=(42, 43), groups=2, axis="routing_strategy", heterogeneous=False):
    servers = [
        {"server_id": 3, "processing_rate": 1.0},
        {"server_id": 8, "processing_rate": 2.0 if heterogeneous else 1.0},
    ]
    experiments = []
    for i in range(groups):
        config = ServerSimulationConfig(
            requests=count,
            seed=seeds[0],
            servers=tuple(
                servers
                if axis == "routing_strategy"
                else [{"server_id": j + 1} for j in range(i + 1)]
            ),
            routing_strategy="least_outstanding"
            if axis == "routing_strategy" and i
            else "round_robin",
        )
        experiments.append({"group_id": f"group_{i}", "config": config})
    return run_server_comparison(
        ServerComparisonRequest(comparison_axis=axis, seeds=seeds, experiments=tuple(experiments))
    )


def open_page(browser, tmp_path, result, **kwargs):
    path = tmp_path / "visualization.html"
    path.write_bytes(render_html(result, **kwargs).encode())
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    requests = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready")
    assert not page.locator("#error").is_visible(), page.locator("#error").inner_text()
    assert not errors
    assert all(url.startswith("file:") for url in requests)
    return page


def check_pose(actual, expected):
    for key in (
        "timestamp",
        "event_cursor",
        "width",
        "height",
        "not_arrived",
        "waiting",
        "processing",
        "completed",
    ):
        assert actual[key] == expected[key]
    assert actual["servers"] == list(expected["servers"])
    assert len(actual["entities"]) == len(expected["entities"])
    for a, b in zip(actual["entities"], expected["entities"], strict=True):
        assert a == pytest.approx(b)  # nonnumeric fields compare exactly


@pytest.mark.parametrize(
    "strategy,servers", [("round_robin", 1), ("round_robin", 2), ("least_outstanding", 2)]
)
def test_all_m1_event_vectors_and_pose_parity(browser, tmp_path, strategy, servers):
    run = hand_run(strategy, servers)
    page = open_page(browser, tmp_path, run)
    replay = ServerEventReplay(run)
    scene = ServerSceneTimeline(run)
    times = sorted({e.timestamp for e in run.events} | {0.12, 1.12, 3.12, 4.12})
    for t in times + list(reversed(times)):
        actual = page.evaluate("t=>{Sim2Clip.seek(t);return Sim2Clip.getState().snapshots[0]}", t)
        reference = asdict(replay.seek(t))
        for key in reference:
            expected = reference[key]
            if isinstance(expected, dict):
                expected = {
                    str(k): list(v) if isinstance(v, tuple) else v for k, v in expected.items()
                }
            assert actual[key] == expected
        check_pose(page.evaluate("Sim2Clip.getScenePoses()[0]"), asdict(scene.seek(t)))
    page.close()


def test_same_time_heterogeneous_and_noncontinuous_ids(browser, tmp_path):
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=10,
            arrivals={"mode": "batch"},
            workload={"mode": "constant", "work": 2.0},
            servers=({"server_id": 3}, {"server_id": 8, "processing_rate": 2.0}),
        )
    )
    page = open_page(browser, tmp_path, result)
    for t in (0, 0.12, 1, 2, 3):
        page.evaluate("t=>Sim2Clip.seek(t)", t)
        check_pose(
            page.evaluate("Sim2Clip.getScenePoses()[0]"),
            asdict(ServerSceneTimeline(result).seek(t)),
        )
    page.close()


@pytest.mark.parametrize("count", [1, 10, 1000, 10000])
@pytest.mark.parametrize("arrivals", ["batch", "exponential"])
def test_offline_scale_aggregation_and_seek_budget(browser, tmp_path, count, arrivals):
    run = run_server_simulation(
        ServerSimulationConfig(
            requests=count,
            arrivals={"mode": arrivals},
            workload={"mode": "constant", "work": 1.0},
            servers=({"server_id": 1}, {"server_id": 2}, {"server_id": 3}),
        )
    )
    page = open_page(browser, tmp_path, run)
    observation = page.evaluate(
        """() => {const times=[];
              for(let i=0;i<20;i++){const t=performance.now();
                Sim2Clip.seek(i%2?0:Sim2Clip.getState().endTime*.7);
                times.push(performance.now()-t);}
              return {times,pose:Sim2Clip.getScenePoses()[0],
                      heap:performance.memory?.usedJSHeapSize};}"""
    )
    pose = observation["pose"]
    assert pose["waiting"] + pose["processing"] + pose["completed"] + pose["not_arrived"] == count
    assert len(pose["entities"]) <= 24
    assert sum(s["queue_visible"] + s["queue_hidden"] for s in pose["servers"]) == pose["waiting"]
    assert (
        max(observation["times"]) < 1500
    )  # catches quadratic replay/draw regressions, not hardware benchmarking
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime)")
    assert page.evaluate("Sim2Clip.getScenePoses()[0].completed") == count
    assert page.evaluate("Sim2Clip.getScenePoses()[0].entities.length") == 0
    page.close()


def test_self_consistent_hash_with_wrong_generated_seed_is_rejected(browser, tmp_path):
    run = run_server_simulation(ServerSimulationConfig(requests=10))
    page = open_page(browser, tmp_path, run)
    rejected = page.evaluate("""async () => {
      const run=structuredClone(Sim2Clip.getResult());run.config.seed=43;
      run.result_id=await Sim2ClipServerReplay.hash({contract:'server-lb-result-v1',
        config:run.config,workload_hash:run.workload_hash,
        workload_source:run.workload_source,metadata:run.metadata});
      try{await Sim2Clip.loadEvidence(run);return false;}catch(e){return e.message;}
    }""")
    assert "seed" in rejected
    page.close()


def test_maximum_server_pool_scrolls_without_missing_nodes(browser, tmp_path):
    run = run_server_simulation(
        ServerSimulationConfig(
            requests=100,
            servers=tuple(
                {"server_id": i * 3 + 1, "processing_rate": float(i + 1)} for i in range(32)
            ),
        )
    )
    page = open_page(browser, tmp_path, run)
    poses = page.evaluate("Sim2Clip.getScenePoses()")
    assert len(poses[0]["servers"]) == 32
    page.locator(".scene-viewport").evaluate("el=>el.scrollTop=el.scrollHeight")
    assert page.locator(".scene-viewport").evaluate("el=>el.scrollTop>0")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.close()


@pytest.mark.parametrize(
    "axis,groups,heterogeneous",
    [("routing_strategy", 2, False), ("routing_strategy", 2, True), ("server_pool", 3, False)],
)
def test_group_seed_switching_shared_clock_and_summary(
    browser, tmp_path, axis, groups, heterogeneous
):
    result = comparison(count=30, groups=groups, axis=axis, heterogeneous=heterogeneous)
    page = open_page(browser, tmp_path, result)
    for seed in result.seeds:
        page.locator("#seed").select_option(str(seed))
        for group in [e.group_id for e in result.request.experiments[1:]]:
            page.locator("#candidate").select_option(group)
            selected = [
                r.result
                for r in result.results
                if r.group_id in (result.baseline_group_id, group) and r.result.config.seed == seed
            ]
            horizon = max(r.metrics.simulation_end_time for r in selected)
            for t in (min(r.metrics.simulation_end_time for r in selected), horizon / 3, horizon):
                page.evaluate("t=>Sim2Clip.seek(t)", t)
                poses = page.evaluate("Sim2Clip.getScenePoses()")
                assert [p["timestamp"] for p in poses] == [t, t]
                for p, r in zip(poses, selected, strict=True):
                    check_pose(p, asdict(ServerSceneTimeline(r).seek(t)))
            page.locator("#restart").click()
            assert page.evaluate("Sim2Clip.getState().clock") == 0
    assert "不是合并请求后的 P95" in page.locator("#summary-note").inner_text()
    assert page.locator("#summary-table tbody tr").count() == groups
    page.close()


def test_controls_play_pause_speed_scrub_and_reverse(browser, tmp_path):
    page = open_page(browser, tmp_path, hand_run())
    page.locator("#speed").select_option("8")
    page.locator("#play").click()
    page.wait_for_function("Sim2Clip.getState().clock > .15")
    page.locator("#play").click()
    state = page.evaluate("Sim2Clip.getState()")
    assert state["playing"] is False
    page.wait_for_timeout(50)
    assert page.evaluate("Sim2Clip.getState().clock") == state["clock"]
    continuous = page.evaluate("Sim2Clip.getScenePoses()")
    page.evaluate("t=>Sim2Clip.seek(t)", state["clock"])
    assert page.evaluate("Sim2Clip.getScenePoses()") == continuous
    page.locator("#scrubber").fill("900")
    assert page.evaluate("Sim2Clip.getState().clock") == pytest.approx(9.9)
    page.locator("#scrubber").fill("100")
    assert page.evaluate("Sim2Clip.getState().clock") == pytest.approx(1.1)
    page.locator("#restart").click()
    assert page.evaluate("Sim2Clip.getState().clock") == 0
    page.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "metric",
        "route",
        "sequence",
        "hash",
        "extra",
        "scenario",
        "summary",
        "difference",
        "seed",
        "config",
    ],
)
def test_browser_rejects_corrupt_evidence_and_clears_old_scene(browser, tmp_path, mutation):
    result = comparison()
    page = open_page(browser, tmp_path, result)
    data = result.model_dump(mode="json")
    run = data["results"][0]["result"]
    if mutation == "metric":
        run["metrics"]["average_response_time"] += 1
    elif mutation == "route":
        run["request_records"][0]["server_id"] = 8
    elif mutation == "sequence":
        run["events"][0]["sequence"] = 2
    elif mutation == "hash":
        run["workload_hash"] = "a" * 64
    elif mutation == "extra":
        run["events"][0]["unknown"] = 0
    elif mutation == "scenario":
        data["scenario"] = "queue"
    elif mutation == "summary":
        data["summaries"][0]["p95_response_time"]["mean"] += 1
    elif mutation == "difference":
        data["paired_differences"][0]["average_response_time_difference"] += 1
    elif mutation == "seed":
        data["seeds"] = [43, 42]
    else:
        data["request"]["experiments"][1]["config"]["workload"]["mean_work"] = 1
    rejected = page.evaluate(
        "async d=>{try{await Sim2Clip.loadEvidence(d);return false;}catch(e){return e.message;}}",
        data,
    )
    assert rejected and page.locator("#error").is_visible()
    assert page.locator("canvas").count() == 0 and page.locator("#play").is_disabled()
    assert page.evaluate("Sim2Clip.getState().snapshots") == []
    page.close()


def test_valid_new_json_load_single_and_safe_text(browser, tmp_path):
    page = open_page(
        browser, tmp_path, comparison(), context={"report": "</script><img src=x onerror=alert(1)>"}
    )
    assert page.locator(".report img").count() == 0
    run = hand_run("least_outstanding")
    path = tmp_path / "single.json"
    path.write_bytes(run.model_dump_json().encode())
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("Sim2Clip.getState().selected.length===1")
    assert page.locator("canvas").count() == 1 and not page.locator("#candidate-label").is_visible()
    assert page.evaluate("Sim2Clip.getState().clock") == 0
    assert "单组模式" in page.locator("#summary-note").inner_text()
    page.close()


def test_invalid_json_upload_pauses_and_removes_previous_scene(browser, tmp_path):
    page = open_page(browser, tmp_path, hand_run())
    page.locator("#play").click()
    bad = tmp_path / "invalid.json"
    bad.write_bytes(b"not json")
    page.locator("#load-file").set_input_files(str(bad))
    page.wait_for_function("document.getElementById('error').hidden===false")
    assert page.locator("canvas").count() == 0
    assert page.evaluate("Sim2Clip.getState().playing") is False
    assert page.evaluate("Sim2Clip.getResult()") is None
    page.close()


def test_queue_json_is_not_reskinned_and_agent_timeline_is_real_text(
    browser, tmp_path, hand_result
):
    timeline = [
        {
            "name": "MCP Tool Call",
            "timestamp": "2026-10-09T09:00:00Z",
            "status": "completed",
            "round": 1,
            "reason": "fixture tool receipt",
            "tool_calls": [
                {"name": "compare_server_simulations", "id": "r1-c0", "status": "success"}
            ],
        }
    ]
    page = open_page(browser, tmp_path, hand_run(), timeline=timeline)
    assert "compare_server_simulations · r1-c0 · success" in page.locator("#timeline").inner_text()
    assert "2026-10-09T09:00:00Z" in page.locator("#timeline").inner_text()
    rejected = page.evaluate(
        "async d=>{try{await Sim2Clip.loadEvidence(d);return false;}catch(e){return e.message;}}",
        hand_result.model_dump(mode="json"),
    )
    assert "server_load_balancing" in rejected
    assert page.locator("canvas").count() == 0
    page.close()


def test_animation_frame_before_play_timestamp_cannot_reverse_clock(browser, tmp_path):
    path = tmp_path / "frame-clock.html"
    path.write_bytes(render_html(hand_run()).encode())
    page = browser.new_page()
    page.add_init_script("""performance.now=()=>100;
      window.requestAnimationFrame=fn=>{window.frameCallback=fn;return 1;};""")
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready")
    page.evaluate("Sim2Clip.play(); window.frameCallback(90)")
    assert page.evaluate("Sim2Clip.getState().clock") == 0
    page.evaluate("window.frameCallback(200)")
    assert page.evaluate("Sim2Clip.getState().clock") > 0
    page.close()
