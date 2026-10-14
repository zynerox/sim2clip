"""Capture and audit the Phase 7.2 browser preview from saved evidence only.

Run with the project interpreter, --result, --output and optional --timeline /
--report. Chromium must be available; failure is reported, never skipped.
"""

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from sim2clip.visualization.data import load_result, select_demo_runs
from sim2clip.visualization.html import render_html
from sim2clip.visualization.replay import EventReplay
from sim2clip.visualization.scene import SceneTimeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = load_result(args.result)
    runs = select_demo_runs(result)
    horizon = max(r.metrics.simulation_end_time for r in runs)
    replay = EventReplay(runs[0])
    times = sorted({e.timestamp for e in runs[0].events})
    peak = max(times, key=lambda t: len(replay.seek(t).queue))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    timeline = json.loads(args.timeline.read_text(encoding="utf-8")) if args.timeline else []
    context = {"report": args.report.read_text(encoding="utf-8")} if args.report else {}
    html_path = output / "visualization.html"
    html_path.write_text(render_html(result, context=context, timeline=timeline), encoding="utf-8")
    if args.result.resolve() != output / "result.json":
        shutil.copyfile(args.result, output / "result.json")
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
    errors: list[str] = []
    requests: list[str] = []
    captures: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1150}, device_scale_factor=1)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(html_path.as_uri())
        assert not page.locator("#error").is_visible()

        def capture(name: str, timestamp: float | None = None) -> None:
            if timestamp is not None:
                page.evaluate("t=>Sim2Clip.seek(t)", timestamp)
            state = page.evaluate("Sim2Clip.getState()")
            poses = page.evaluate("Sim2Clip.getScenePoses()")
            assert poses[0]["timestamp"] == state["clock"]
            if len(poses) > 1:
                assert poses[1]["timestamp"] == state["clock"]
            for pose, run in zip(poses, runs, strict=True):
                reference = asdict(SceneTimeline(run, end_time=horizon).seek(state["clock"]))
                assert pose["queue_length"] == reference["queue_length"]
                assert pose["completed"] == reference["completed"]
                for index, person in enumerate(pose["entities"]):
                    assert 0 <= person["x"] <= 720 and 46 <= person["y"] <= 440
                    assert all(
                        abs(person["x"] - other["x"]) >= 28 - 1e-8
                        or abs(person["y"] - other["y"]) >= 48 - 1e-8
                        for other in pose["entities"][index + 1 :]
                    )
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            path = output / name
            page.screenshot(path=str(path))
            captures.append({"file": name, "simulation_time": state["clock"], "poses": poses})

        capture("initial.png", 0)
        capture("queue-peak.png", peak)
        difference = 850 if horizon > 850 and runs[0].config.customers == 100 else horizon / 3
        capture("ab-difference.png", difference)
        # This screenshot comes from advancing requestAnimationFrame playback,
        # followed by pause, rather than seeking directly to the midpoint.
        page.locator("#restart").click()
        page.locator("#play").click()
        page.wait_for_function("Sim2Clip.getState().clock>=Sim2Clip.getState().endTime/2")
        page.locator("#play").click()
        capture("playback-middle.png")
        paused = page.evaluate("Sim2Clip.getScenePoses()")
        assert page.evaluate("""async()=>{
          const before=JSON.stringify(Sim2Clip.getScenePoses());
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          return JSON.stringify(Sim2Clip.getScenePoses())===before;
        }""")
        page.evaluate("Sim2Clip.seek(0)")
        page.evaluate("t=>Sim2Clip.seek(t)", paused[0]["timestamp"])
        assert page.evaluate("Sim2Clip.getScenePoses()") == paused
        capture("motion-detail.png", 12)
        page.set_viewport_size({"width": 390, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        capture("mobile-regression.png", difference)
        browser_version = browser.version
        browser.close()
    assert not errors and all(url.startswith("file:") for url in requests)
    ledger = {
        "browser": browser_version,
        "executable": executable,
        "result_sha256": hashlib.sha256(args.result.read_bytes()).hexdigest(),
        "page_errors": errors,
        "external_requests": [],
        "checks": [
            "shared clock",
            "live playback",
            "pause freezes positions",
            "reverse seek",
            "no overlapping character footprints",
            "population counts",
            "no horizontal overflow",
        ],
        "screenshots": captures,
    }
    (output / "browser-validation.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "browser": browser_version,
                "captures": len(captures),
                "page_errors": errors,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
