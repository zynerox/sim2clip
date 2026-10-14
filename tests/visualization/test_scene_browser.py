"""Real Chromium motion, arbitrary seeks, rebuilding and Python/JS pose parity."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from sim2clip.simulation import SimulationConfig, run_simulation
from sim2clip.simulation.schemas import ComparisonResult, CustomerSample
from sim2clip.visualization.data import select_demo_runs
from sim2clip.visualization.scene import SceneTimeline


@pytest.fixture
def scene_page(browser: Any, demo_files: dict[str, str]) -> Any:
    page = browser.new_page(viewport={"width": 1440, "height": 1150})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(Path(demo_files["visualization"]).as_uri())
    assert not page.locator("#error").is_visible()
    yield page
    assert errors == []
    page.close()


def assert_nested_equal(actual: Any, expected: Any) -> None:
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_nested_equal(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected, strict=True):
            assert_nested_equal(a, b)
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-7)
    else:
        assert actual == expected


def test_all_event_boundaries_and_motion_midpoints_match_python(
    scene_page: Any, official: ComparisonResult
) -> None:
    runs = select_demo_runs(official)
    horizon = max(r.metrics.simulation_end_time for r in runs)
    for run in runs:
        events = sorted({e.timestamp for e in run.events})
        times = sorted(
            set(
                events
                + [t + 0.001 for t in events]
                + [(a + b) / 2 for a, b in zip(events, events[1:], strict=False)]
            )
        )
        observed = scene_page.evaluate(
            "({run,times,horizon})=>{const scene=Sim2Clip.createScene(run,horizon);"
            "return times.map(t=>scene.seek(t));}",
            {"run": run.model_dump(mode="json"), "times": times, "horizon": horizon},
        )
        scene = SceneTimeline(run, end_time=horizon)
        for actual, timestamp in zip(observed, times, strict=True):
            assert_nested_equal(actual, asdict(scene.seek(timestamp)))


def test_reverse_seek_and_scrubber_restore_exact_positions(scene_page: Any) -> None:
    original = scene_page.evaluate("Sim2Clip.getScenePoses()")
    scene_page.locator("#scrubber").fill("500")
    scene_page.locator("#scrubber").dispatch_event("input")
    middle = scene_page.evaluate("Sim2Clip.getScenePoses()")
    scene_page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime)")
    scene_page.locator("#scrubber").fill("500")
    scene_page.locator("#scrubber").dispatch_event("input")
    assert scene_page.evaluate("Sim2Clip.getScenePoses()") == middle
    scene_page.locator("#restart").click()
    assert scene_page.evaluate("Sim2Clip.getScenePoses()") == original
    assert scene_page.evaluate("Sim2Clip.getScenePoses().every(p=>p.completed===0)")


def test_actual_playback_moves_customers_and_pause_freezes_them(scene_page: Any) -> None:
    initial = scene_page.locator("canvas").first.screenshot()
    scene_page.locator("#play").click()
    scene_page.wait_for_function("Sim2Clip.getState().clock>12")
    scene_page.locator("#play").click()
    assert initial != scene_page.locator("canvas").first.screenshot()
    assert scene_page.evaluate("""async()=>{
      const before=JSON.stringify(Sim2Clip.getScenePoses());
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return !Sim2Clip.getState().playing&&JSON.stringify(Sim2Clip.getScenePoses())===before;
    }""")
    state = scene_page.evaluate("Sim2Clip.getState()")
    poses = scene_page.evaluate("Sim2Clip.getScenePoses()")
    for index, pose in enumerate(poses):
        expected = scene_page.evaluate(
            "({id,t,end})=>Sim2Clip.createScene(Sim2Clip.getResult().results.find(r=>"
            "r.result_id===id),end).seek(t)",
            {"id": state["selected"][index], "t": state["clock"], "end": state["endTime"]},
        )
        assert pose == expected


def test_double_speed_preserves_shared_event_states(
    scene_page: Any, official: ComparisonResult
) -> None:
    scene_page.locator("#speed").select_option("2")
    scene_page.locator("#play").click()
    scene_page.wait_for_function("Sim2Clip.getState().clock>80")
    scene_page.locator("#play").click()
    state = scene_page.evaluate("Sim2Clip.getState()")
    poses = scene_page.evaluate("Sim2Clip.getScenePoses()")
    assert poses[0]["timestamp"] == poses[1]["timestamp"] == state["clock"]
    for run, pose in zip(select_demo_runs(official), poses, strict=True):
        assert_nested_equal(
            pose, asdict(SceneTimeline(run, end_time=state["endTime"]).seek(state["clock"]))
        )


def test_seed_and_loaded_result_rebuild_scene_without_old_customers(
    scene_page: Any, tmp_path: Path
) -> None:
    scene_page.evaluate("Sim2Clip.seek(850)")
    scene_page.locator("#seed").select_option("43")
    assert scene_page.evaluate("Sim2Clip.getState().clock") == 0
    assert scene_page.evaluate("Sim2Clip.getScenePoses().every(p=>p.completed===0)")
    scene_page.evaluate("Sim2Clip.seek(850)")
    actual = scene_page.evaluate("Sim2Clip.getScenePoses()")
    expected = scene_page.evaluate("""()=>{
      const s=Sim2Clip.getState();return s.selected.map(id=>Sim2Clip.createScene(
        Sim2Clip.getResult().results.find(r=>r.result_id===id),s.endTime).seek(s.clock));
    }""")
    assert actual == expected
    run = run_simulation(
        SimulationConfig(cashiers=2, customers=3, seed=7),
        tuple(
            CustomerSample(customer_id=i, arrival_time=0, service_time=5) for i in (200, 401, 999)
        ),
    )
    path = tmp_path / "new.json"
    path.write_text(run.model_dump_json(), encoding="utf-8")
    scene_page.locator("#load-file").set_input_files(str(path))
    scene_page.wait_for_function("!!Sim2Clip.getResult().result_id")
    assert scene_page.evaluate("Sim2Clip.getState().clock") == 0
    pose = scene_page.evaluate("Sim2Clip.getScenePoses()[0]")
    assert_nested_equal(pose, asdict(SceneTimeline(run).seek(0)))
    assert all(p["customer_id"] in (200, 401, 999) for p in pose["entities"])


@pytest.mark.parametrize("cashiers", [1, 3, 8, 100])
def test_tied_events_and_aggregation_match_python(scene_page: Any, cashiers: int) -> None:
    run = run_simulation(
        SimulationConfig(cashiers=cashiers, customers=120),
        tuple(CustomerSample(customer_id=i, arrival_time=0, service_time=10) for i in range(120)),
    )
    times = [0, 1.5, 5, 10, 10.1, 12, 0, 20, run.metrics.simulation_end_time]
    observed = scene_page.evaluate(
        "({run,times})=>{const scene=Sim2Clip.createScene(run);"
        "return times.map(t=>scene.seek(t));}",
        {"run": run.model_dump(mode="json"), "times": times},
    )
    scene = SceneTimeline(run)
    for pose, timestamp in zip(observed, times, strict=True):
        assert_nested_equal(pose, asdict(scene.seek(timestamp)))
        assert pose["queue_visible"] + pose["queue_hidden"] == pose["queue_length"]
        assert len({p["customer_id"] for p in pose["entities"]}) == len(pose["entities"])
    if cashiers > 6:
        labels = scene_page.evaluate(
            """run=>{
              Sim2Clip.loadEvidence(run);
              const context=document.querySelector('canvas').getContext('2d');
              const original=context.fillText, labels=[];
              context.fillText=function(...args){labels.push(args[0]);original.apply(this,args);};
              try{Sim2Clip.seek(0);}finally{context.fillText=original;}
              return labels;
            }""",
            run.model_dump(mode="json"),
        )
        assert f"另有 {cashiers - 6} 台 · {cashiers - 6} 台忙碌" in labels
