"""Capture Phase 7.3 from saved results in actual Chromium, without rerunning models.

Use a NEW --output directory. Native stage PNGs and real 360/390 CSS px previews
are separate artifacts; the DOM/canvas stage contains no playback controls.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from sim2clip.visualization.data import load_result
from sim2clip.visualization.html import render_html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--time", nargs=2, action="append", default=[], metavar=("LABEL", "SECONDS")
    )
    args = parser.parse_args()
    result = load_result(args.result)
    source_sha = hashlib.sha256(args.result.read_bytes()).hexdigest()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    timeline = json.loads(args.timeline.read_text(encoding="utf-8")) if args.timeline else []
    context = {"report": args.report.read_text(encoding="utf-8")} if args.report else {}
    html = output / "visualization.html"
    html.write_text(render_html(result, timeline=timeline, context=context), encoding="utf-8")
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
    errors = []
    requests = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 2300}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(html.as_uri())
        page.evaluate("Sim2Clip.ready || null")
        assert not page.locator("#error").is_visible()
        page.locator("#layout").select_option("vertical")
        page.evaluate("document.getElementById('vertical-preview').style.width='1080px'")
        page.wait_for_function("document.getElementById('vertical-preview').clientHeight===1920")
        times = page.evaluate("""() => {
          const result=Sim2Clip.getResult(), s=Sim2Clip.getState();
          const all=result.results?.map(x=>x.result||x);
          const run=all?.find(x=>x.result_id===s.selected[0])||result;
          const server=!!Sim2Clip.scenario_id;
          const settled=run.events.filter((e,i)=>run.events[i+1]?.timestamp!==e.timestamp);
          const peak=settled.reduce((best,e)=>{
            const n=server?e.total_queue_length_after_event:e.queue_length_after_event;
            return n>best.n?{n,t:e.timestamp}:best;
          },{n:-1,t:0}).t;
          return {initial:0,routing:run.events.find(e=>e.timestamp>0)?.timestamp||0,
                  peak,middle:s.endTime/2,end:s.endTime};
        }""")
        for label, timestamp in args.time:
            if not label.replace("-", "").isalnum():
                parser.error("Screenshot labels must be alphanumeric")
            times[label] = float(timestamp)
        for label, timestamp in times.items():
            page.evaluate("t=>Sim2Clip.seek(t)", timestamp)
            page.locator("#vertical-preview").screenshot(path=str(output / f"{label}-1080.png"))
            captures.append(
                {
                    "file": f"{label}-1080.png",
                    "state": page.evaluate("Sim2Clip.getState()"),
                    "poses": page.evaluate("Sim2Clip.getStagePoses()"),
                }
            )
        page.evaluate("t=>Sim2Clip.seek(t)", times["middle"])
        for width in (360, 390):
            page.evaluate("document.getElementById('vertical-preview').style.width=''")
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_function(
                "document.getElementById('vertical-preview').clientWidth===innerWidth"
            )
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.locator("#vertical-preview").screenshot(path=str(output / f"mobile-{width}.png"))
            page.locator("#vertical-preview").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"viewport-{width}.png"))
            captures.append(
                {
                    "file": f"mobile-{width}.png",
                    "width": width,
                    "state": page.evaluate("Sim2Clip.getState()"),
                }
            )
        page.set_viewport_size({"width": 1440, "height": 1150})
        before = page.evaluate("Sim2Clip.getState()")
        page.locator("#layout").select_option("desktop")
        assert page.evaluate("Sim2Clip.getState()") == before
        page.screenshot(path=str(output / "desktop.png"))
        assert not errors and all(url.startswith("file:") for url in requests)
        proof = {
            "browser": browser.version,
            "source": str(args.result),
            "source_sha256": source_sha,
            "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
            "logical_stage": [1080, 1920],
            "errors": errors,
            "network": requests,
            "captures": captures,
        }
        (output / "browser-proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()
    assert hashlib.sha256(args.result.read_bytes()).hexdigest() == source_sha
    print(
        json.dumps({"output": str(output), "browser": proof["browser"], "captures": len(captures)})
    )


if __name__ == "__main__":
    main()
