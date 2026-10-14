"""Capture saved server evidence in real Chromium; never executes an engine.

python tests/visualization/capture_server_scene.py --result .../result.json --output .../browser
"""

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from statistics import median
from time import perf_counter

from playwright.sync_api import sync_playwright

from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.visualization.data import load_result
from sim2clip.visualization.html import render_html
from sim2clip.visualization.server_scene import ServerSceneTimeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = load_result(args.result)
    if not isinstance(result, (ServerSimulationResult, ServerComparisonResult)):
        parser.error("Server evidence required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    html_path = output / "visualization.html"
    if html_path.exists():
        parser.error("Choose a new capture directory; existing proof is preserved")
    timeline = json.loads(args.timeline.read_text(encoding="utf-8")) if args.timeline else []
    context = {"report": args.report.read_text(encoding="utf-8")} if args.report else {}
    html_path.write_bytes(render_html(result, context=context, timeline=timeline).encode())
    if isinstance(result, ServerComparisonResult):
        seed = result.seeds[0]
        selected = [
            r.result
            for r in result.results
            if r.result.config.seed == seed
            and r.group_id in (result.baseline_group_id, result.request.experiments[1].group_id)
        ]
    else:
        selected = [result]
    runs = [ServerSceneTimeline(r) for r in selected]
    horizon = max(r.metrics.simulation_end_time for r in selected)
    settled = [
        e
        for i, e in enumerate(selected[0].events)
        if i == len(selected[0].events) - 1 or selected[0].events[i + 1].timestamp != e.timestamp
    ]
    peak = max(settled, key=lambda e: e.total_queue_length_after_event).timestamp
    configured = os.getenv("SIM2CLIP_BROWSER_EXECUTABLE")
    candidates = (
        [Path(configured)]
        if configured
        else [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
    )
    executable = next((str(p) for p in candidates if p.is_file()), None)
    captures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1120}, device_scale_factor=1)
        requests: list[str] = []
        errors: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.on("pageerror", lambda e: errors.append(str(e)))
        start = perf_counter()
        page.goto(html_path.as_uri())
        page.evaluate("Sim2Clip.ready")
        startup_ms = (perf_counter() - start) * 1000
        assert not page.locator("#error").is_visible(), page.locator("#error").inner_text()
        assert not errors and all(url.startswith("file:") for url in requests)

        def capture(name: str, time: float) -> None:
            page.evaluate("t=>Sim2Clip.seek(t)", time)
            poses = page.evaluate("Sim2Clip.getScenePoses()")
            reference = [asdict(scene.seek(time)) for scene in runs]
            for a, b in zip(poses, reference, strict=True):
                for key in ("event_cursor", "not_arrived", "waiting", "processing", "completed"):
                    assert a[key] == b[key]
                assert a["servers"] == list(b["servers"])
                assert len(a["entities"]) == len(b["entities"])
                for entity, expected in zip(a["entities"], b["entities"], strict=True):
                    for key in ("request_id", "server_id", "status", "moving"):
                        assert entity[key] == expected[key]
                    assert abs(entity["x"] - expected["x"]) < 1e-8
                    assert abs(entity["y"] - expected["y"]) < 1e-8
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(output / name), full_page=True)
            captures.append({"file": name, "timestamp": time, "poses": poses})

        capture("initial.png", 0)
        records = selected[0].request_records
        target = next((r for r in records if r.arrival_time > 0), records[0])
        boundary = (
            target.processing_start_time if target.wait_time > 0 else target.processing_end_time
        )
        capture(
            "routing-processing.png",
            target.arrival_time + min(0.12, (boundary - target.arrival_time) * 0.2),
        )
        capture("queue-backlog.png", peak)
        capture(
            "ab-comparison.png",
            min(horizon, 3.12) if selected[0].config.requests == 3 else horizon * 0.4,
        )
        capture("completed.png", horizon)
        durations = page.evaluate("""() => {const end=Sim2Clip.getState().endTime,times=[];
          for(let i=0;i<30;i++){const start=performance.now();Sim2Clip.seek(i%2?0:end*.7);
            times.push(performance.now()-start);}return times;}""")
        # Actual frame playback then pause; direct seek to that time must reproduce the same pose.
        page.locator("#restart").click()
        page.locator("#speed").select_option("8")
        start = perf_counter()
        page.locator("#play").click()
        page.wait_for_timeout(1000)
        page.locator("#play").click()
        state = page.evaluate("Sim2Clip.getState()")
        continuous = page.evaluate("Sim2Clip.getScenePoses()")
        capture("continuous-playback.png", state["clock"])
        assert page.evaluate("Sim2Clip.getScenePoses()") == continuous
        playback_wall_ms = (perf_counter() - start) * 1000
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")
        memory = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        assert not errors, errors
        proof = {
            "browser": browser.version,
            "source": str(args.result.resolve()),
            "html_bytes": html_path.stat().st_size,
            "html_sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
            "source_sha256": hashlib.sha256(args.result.read_bytes()).hexdigest(),
            "request_runs": sum(r.result.config.requests for r in result.results)
            if isinstance(result, ServerComparisonResult)
            else result.config.requests,
            "embedded_events": sum(len(r.result.events) for r in result.results)
            if isinstance(result, ServerComparisonResult)
            else len(result.events),
            "events": sum(len(r.events) for r in selected),
            "startup_ms": startup_ms,
            "seek_draw_median_ms": median(durations),
            "seek_draw_max_ms": max(durations),
            "js_heap_used_bytes": memory.get("JSHeapUsedSize"),
            "js_heap_total_bytes": memory.get("JSHeapTotalSize"),
            "playback_wall_ms": playback_wall_ms,
            "playback_clock": state["clock"],
            "external_requests": [],
            "page_errors": errors,
            "captures": captures,
        }
        (output / "proof.json").write_bytes(
            json.dumps(proof, ensure_ascii=False, indent=2).encode()
        )
        print(json.dumps({k: v for k, v in proof.items() if k != "captures"}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
