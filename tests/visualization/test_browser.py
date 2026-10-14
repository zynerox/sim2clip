"""Real Chromium: JS/Python parity, interactions, loaded data and responsive layout."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from sim2clip.simulation import SimulationConfig, run_comparison
from sim2clip.simulation.schemas import ComparisonResult, SimulationResult
from sim2clip.visualization.html import render_html
from sim2clip.visualization.replay import EventReplay


@pytest.fixture
def page(browser: Any, demo_files: dict[str, str]) -> Any:
    instance = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    instance.on("pageerror", lambda exc: errors.append(str(exc)))
    instance.goto(Path(demo_files["visualization"]).as_uri())
    yield instance
    assert not errors
    instance.close()


def normalized(snapshot: Any) -> dict[str, Any]:
    value = asdict(snapshot)
    value["queue"] = list(value["queue"])
    value["cashiers"] = list(value["cashiers"].values())
    value["customer_states"] = {str(k): v for k, v in value["customer_states"].items()}
    return value


def test_browser_event_replay_matches_python_at_every_event(
    page: Any, official: ComparisonResult
) -> None:
    for run in official.results[:2]:
        times = sorted({e.timestamp for e in run.events})
        observed = page.evaluate(
            "({run,times})=>{const r=Sim2Clip.createReplay(run);return times.map(t=>r.seek(t));}",
            {"run": run.model_dump(mode="json"), "times": times},
        )
        replay = EventReplay(run)
        assert observed == [normalized(replay.seek(t)) for t in times]


def test_play_pause_scrub_and_shared_completion(page: Any, official: ComparisonResult) -> None:
    assert not page.locator("#error").is_visible()
    page.locator("#play").click()
    page.wait_for_function("Sim2Clip.getState().clock>1")
    page.locator("#play").click()
    paused = page.evaluate("Sim2Clip.getState()")
    assert paused["clock"] > 0 and not paused["playing"]
    page.locator("#scrubber").fill("500")
    page.locator("#scrubber").dispatch_event("input")
    state = page.evaluate("Sim2Clip.getState()")
    assert state["clock"] == pytest.approx(state["endTime"] / 2)
    assert state["snapshots"][0]["timestamp"] == state["snapshots"][1]["timestamp"]
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime)")
    assert page.locator(".done").all_text_contents() == ["100", "100"]
    page.locator("#restart").click()
    assert page.evaluate("Sim2Clip.getState().clock") == 0


def test_different_groups_and_seeds_then_single_result_file(
    page: Any, tmp_path: Path, hand_result: SimulationResult
) -> None:
    other = run_comparison(
        tuple(SimulationConfig(cashiers=c, customers=8) for c in (2, 4, 6)), seeds=(7, 9)
    )
    path = tmp_path / "different.json"
    path.write_text(other.model_dump_json())
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("Sim2Clip.getResult().seeds[0]===7")
    assert page.locator("#seed").input_value() == "7"
    page.locator("#candidate").select_option("6")
    assert page.locator("h3").all_text_contents() == ["2 个收银台", "6 个收银台"]
    page.locator("#seed").select_option("9")
    chosen = page.evaluate("Sim2Clip.getState().selected")
    assert chosen == [
        r.result_id for r in other.results if r.config.seed == 9 and r.config.cashiers in (2, 6)
    ]
    path.write_text(hand_result.model_dump_json())
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("!!Sim2Clip.getResult().result_id")
    assert page.locator(".panel").count() == 1
    assert not page.locator("#candidate-label").is_visible()
    assert "没有 Agent" in page.locator("#timeline").inner_text()


@pytest.mark.parametrize("damage", ["missing", "queue", "unknown_field"])
def test_bad_loaded_json_keeps_valid_evidence_and_shows_error(
    page: Any, tmp_path: Path, hand_result: SimulationResult, damage: str
) -> None:
    old = page.evaluate("Sim2Clip.getResult().comparison_id")
    value = hand_result.model_dump(mode="json")
    if damage == "missing":
        value["events"].pop()
    elif damage == "queue":
        value["events"][0]["queue_length_after_event"] = 9
    else:
        value["config"]["made_up"] = True
    import json

    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value))
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("!document.getElementById('error').hidden")
    assert "无法回放" in page.locator("#error").inner_text()
    assert page.evaluate("Sim2Clip.getResult().comparison_id") == old


@pytest.mark.parametrize("width", [1440, 390])
def test_responsive_page_has_no_horizontal_overflow(page: Any, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 1000})
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    assert page.locator(".panel").count() == 2


def test_query_text_is_not_executed_and_no_network_assets(
    browser: Any, hand_result: SimulationResult, tmp_path: Path
) -> None:
    html = render_html(
        hand_result, context={"experiment_goal": "</script><script>window.PWNED=1</script>"}
    )
    path = tmp_path / "safe.html"
    path.write_text(html, encoding="utf-8")
    page = browser.new_page()
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(path.as_uri())
    assert page.evaluate("typeof window.PWNED") == "undefined"
    assert "window.PWNED" in page.locator("#goal").inner_text()
    assert all(url.startswith("file:") for url in requests)
    page.close()
