"""JSON identity, complete GIF audit, packaging assets and truthful execution times."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from sim2clip.simulation.schemas import ComparisonResult, SimulationResult
from sim2clip.visualization.data import VisualizationError, load_result
from sim2clip.visualization.gif import frame_schedule, render_gif
from sim2clip.visualization.html import render_html
from sim2clip.visualization.pipeline import export_demo
from sim2clip.visualization.replay import EventReplay
from sim2clip.visualization.timeline import execution_timeline


def embedded_data(html: str) -> dict[str, Any]:
    match = re.search(
        r'<script type="application/json" id="experiment-data">(.*?)</script>', html, re.S
    )
    assert match
    return dict(json.loads(match.group(1)))


def test_html_preserves_all_shared_results_and_events(official: ComparisonResult) -> None:
    html = render_html(official)
    payload = embedded_data(html)
    assert payload["result"] == official.model_dump(mode="json")
    assert payload["schemas"]["ComparisonResult"] == ComparisonResult.model_json_schema()
    assert "__PAYLOAD__" not in html and "/*__SCRIPT__*/" not in html
    assert 'src="https://' not in html and 'href="https://' not in html
    assert all(len(r["events"]) == 300 for r in payload["result"]["results"])


def test_embedded_strings_cannot_escape_script(hand_result: SimulationResult) -> None:
    malicious = "</script><script>window.PWNED=1</script>"
    html = render_html(hand_result, context={"experiment_goal": malicious})
    assert malicious not in html
    assert embedded_data(html)["context"]["experiment_goal"] == malicious


def test_gif_frame_states_and_json_provenance(
    demo_files: dict[str, str], official: ComparisonResult
) -> None:
    content = Path(demo_files["result"]).read_bytes()
    assert load_result(Path(demo_files["result"])) == official
    manifest = json.loads(Path(demo_files["gif_manifest"]).read_text())
    assert (
        manifest["seed"] == 42 and manifest["result_sha256"] == hashlib.sha256(content).hexdigest()
    )
    assert (
        manifest["gif_sha256"] == hashlib.sha256(Path(demo_files["gif"]).read_bytes()).hexdigest()
    )
    runs = {r.result_id: r for r in official.results}
    for frame in manifest["frames"]:
        for entry in frame["runs"]:
            state = EventReplay(runs[entry["result_id"]]).seek(frame["simulation_time"])
            assert entry["completed"] == state.completed
            assert entry["queue_length"] == len(state.queue)
            assert entry["events_applied"] == state.events_applied
    with Image.open(demo_files["gif"]) as image:
        assert image.size == (1200, 720) and image.n_frames == len(manifest["frames"])
        assert json.loads(image.info["comment"])["result_sha256"] == manifest["result_sha256"]
        duration = 0
        for index, frame in enumerate(manifest["frames"]):
            image.seek(index)
            assert image.info["duration"] == frame["duration_ms"]
            duration += image.info["duration"]
        assert duration == manifest["duration_ms"]
    assert all(entry["completed"] == 100 for entry in manifest["frames"][-1]["runs"])


def test_gif_reproducible_without_simulation_reexecution(hand_result: SimulationResult) -> None:
    first, manifest = render_gif(hand_result, result_sha256="0" * 64, frames=3)
    second, _ = render_gif(hand_result, result_sha256="0" * 64, frames=3)
    assert first == second and len(manifest["frames"]) == 3


def test_bad_evidence_does_not_publish_fake_html_or_gif(
    tmp_path: Path, hand_result: SimulationResult
) -> None:
    invalid = hand_result.model_copy(update={"events": ()})
    # Public boundaries revalidate even callers using Pydantic's unchecked model_copy.
    with pytest.raises(VisualizationError):
        export_demo(invalid, tmp_path)
    assert not (tmp_path / "demo.gif").exists()


def test_checkpoint_timeline_preserves_time_retry_and_tool_arguments() -> None:
    decisions = [
        {"node": "planner", "round": 1, "action": "propose", "reason": "planned"},
        {"node": "executor", "round": 1, "action": "tool_success", "reason": "run_simulation"},
        {"node": "results", "round": 1, "action": "replan", "reason": "need more seeds"},
    ]
    call = {
        "id": "r1-c0",
        "name": "run_simulation",
        "arguments": {"cashiers": 3},
        "attempt": 1,
        "status": "success",
    }
    history = [
        {
            "timestamp": "2026-10-09T00:00:01+00:00",
            "step": 1,
            "state": {"decision_history": decisions[:1], "tool_calls": []},
        },
        {
            "timestamp": "2026-10-09T00:00:02+00:00",
            "step": 2,
            "state": {"decision_history": decisions[:2], "tool_calls": [call]},
        },
        {
            "timestamp": "2026-10-09T00:00:03+00:00",
            "step": 3,
            "state": {"decision_history": decisions, "tool_calls": [call]},
        },
    ]
    timeline = execution_timeline(history, {})
    assert len(timeline) == 3 and timeline[1]["tool_calls"][0]["arguments"] == {"cashiers": 3}
    assert timeline[1]["timestamp"] == history[1]["timestamp"]
    assert timeline[2]["status"] == "retry" and timeline[2]["name"] == "Result Validator"
    offline = execution_timeline([], {"decision_history": decisions, "tool_calls": [call]})
    assert all(row["timestamp"] is None for row in offline)


def test_frame_schedule_covers_exact_endpoint() -> None:
    frames = frame_schedule(123.456)
    assert frames[0] == (0, 1200) and frames[-1] == (123.456, 3000)
    with pytest.raises(ValueError):
        frame_schedule(1, frames=1)


def test_result_only_timeline_associates_each_tool_with_its_round() -> None:
    decisions = [
        {"node": "executor", "round": 1, "action": "tool_success", "reason": "pilot"},
        {"node": "executor", "round": 2, "action": "tool_success", "reason": "full"},
    ]
    calls = [
        {
            "id": f"r{r}-c0",
            "round": r,
            "name": "compare_simulations",
            "arguments": {"seeds": [42] if r == 1 else [42, 43, 44, 45, 46]},
            "status": "success",
            "attempt": 1,
        }
        for r in (1, 2)
    ]
    rows = execution_timeline([], {"decision_history": decisions, "tool_calls": calls})
    assert [row["tool_calls"][0]["id"] for row in rows] == ["r1-c0", "r2-c0"]
    assert all(row["timestamp"] is None for row in rows)
