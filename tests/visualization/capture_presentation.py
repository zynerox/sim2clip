"""Capture Phase 7.4 using saved results and read-only historical checkpoints.

No model or simulation execution. Requires a new output directory. Complete
30-second playback, native canvas comparison and real phone viewports are checked.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from playwright.sync_api import sync_playwright

from sim2clip.agent.artifacts import ArtifactStore
from sim2clip.visualization.data import load_result
from sim2clip.visualization.html import render_html
from sim2clip.visualization.presentation import PresentationConfig, build_manifest
from sim2clip.visualization.timeline import public_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--thread-id")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--public-question", help="Only explicitly approved public text")
    parser.add_argument(
        "--public-case", choices=("A", "B"), help="Use the already published R1 question"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.public_case:
        if args.thread_id != args.public_case:
            parser.error("Public question case must match the checkpoint thread")
        evidence = Path(__file__).resolve().parents[2] / "docs/assets/real-llm-r1-verification.json"
        cases = json.loads(evidence.read_text(encoding="utf-8"))["cases"]
        args.public_question = next(
            c["actual_input"] for c in cases if c["case_id"] == args.public_case
        )
    result = load_result(args.result)
    timeline = (
        public_rows(json.loads(args.timeline.read_text(encoding="utf-8"))) if args.timeline else []
    )
    state = None
    if args.checkpoint:
        if not args.thread_id or not args.artifact_root:
            parser.error("Checkpoint requires --thread-id and --artifact-root")
        with sqlite3.connect(
            args.checkpoint.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        ) as connection:
            record = connection.execute(
                "SELECT type,checkpoint FROM checkpoints WHERE thread_id=? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (args.thread_id,),
            ).fetchone()
        state = JsonPlusSerializer().loads_typed(record)["channel_values"]
    manifest = build_manifest(
        result,
        timeline=timeline,
        state=state,
        store=ArtifactStore(args.artifact_root) if args.artifact_root else None,
        config=PresentationConfig(default_mode="presentation"),
        public_question=args.public_question,
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    html = output / "visualization.html"
    html.write_text(render_html(result, timeline=timeline, presentation=manifest), encoding="utf-8")
    (output / "result.json").write_bytes(args.result.read_bytes())
    (output / "presentation.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    captures, errors, requests = [], [], []
    candidates = (
        [Path(os.environ["SIM2CLIP_BROWSER_EXECUTABLE"])]
        if os.getenv("SIM2CLIP_BROWSER_EXECUTABLE")
        else [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
    )
    executable = next((str(p) for p in candidates if p.is_file()), None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 2300}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        page.goto(html.as_uri())
        page.evaluate("Sim2Clip.ready || null")
        page.evaluate(
            "Sim2Clip.setLayout('vertical');document.querySelector('.presentation-preview').style.width='1080px'"
        )
        page.wait_for_function(
            "document.querySelector('.presentation-preview').clientHeight===1920"
        )
        for label, p in (
            ("question", 1),
            ("plan", 4.5),
            ("tool", 7),
            ("verify", 9),
            ("middle", 17),
            ("reveal", 25),
        ):
            page.evaluate("p=>Sim2Clip.seekPresentation(p)", p)
            file = output / f"{label}-1080.png"
            page.locator("#presentation-preview").screenshot(path=str(file))
            s = page.evaluate("Sim2Clip.getPresentationState()")
            assert not page.locator("#report,#timeline,#summary-table,#panels").count()
            captures.append(
                {
                    "file": file.name,
                    "state": s,
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
            )
        # Test full progression through the same scheduler the UI uses, at normal speed.
        page.evaluate("Sim2Clip.seekPresentation(0);Sim2Clip.play()")
        page.wait_for_function(
            "Sim2Clip.getPresentationState().presentation_time>16.5", timeout=25000
        )
        page.evaluate("Sim2Clip.pause()")
        observed = page.evaluate("[Sim2Clip.getPresentationState(),Sim2Clip.getStagePoses()]")
        pixels = page.locator(".stage-canvas").first.screenshot()
        page.evaluate(
            "p=>{Sim2Clip.seekPresentation(0);Sim2Clip.seekPresentation(p)}",
            observed[0]["presentation_time"],
        )
        assert page.locator(".stage-canvas").first.screenshot() == pixels
        assert page.evaluate("Sim2Clip.getStagePoses()") == observed[1]
        page.evaluate("Sim2Clip.play()")
        page.wait_for_function("!Sim2Clip.getState().playing", timeout=20000)
        assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 30
        assert page.evaluate("Sim2Clip.getState().clock===Sim2Clip.getState().endTime")
        for width in (360, 390):
            page.evaluate("document.querySelector('.presentation-preview').style.width=''")
            page.set_viewport_size({"width": width, "height": 844})
            for label, p in (("question", 1), ("plan", 4.5), ("middle", 17), ("reveal", 25)):
                page.evaluate("p=>Sim2Clip.seekPresentation(p)", p)
                page.wait_for_function(
                    "document.querySelector('.presentation-preview').clientWidth===innerWidth"
                )
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                file = output / f"{label}-{width}.png"
                page.locator("#presentation-preview").screenshot(path=str(file))
                captures.append(
                    {"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                )
            page.locator("#presentation-preview").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"viewport-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.evaluate("Sim2Clip.setLayout('desktop');Sim2Clip.seekPresentation(17)")
        page.locator("#presentation-preview").screenshot(path=str(output / "desktop-middle.png"))
        assert not errors
        assert all(url.startswith("file:") for url in requests)
        proof = {
            "browser": browser.version,
            "source": str(args.result),
            "source_sha256": hashlib.sha256(args.result.read_bytes()).hexdigest(),
            "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
            "execution": manifest.execution.model_dump(mode="json"),
            "captures": captures,
            "full_playback_seconds": 30,
            "canvas_direct_seek_equal": True,
            "pageerrors": errors,
            "requests": requests,
        }
        (output / "browser-proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()
    print(f"Verified offline presentation: {output}")


if __name__ == "__main__":
    main()
