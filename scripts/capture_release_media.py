"""Record accepted offline Presentation stages; never run a model or simulation.

This is release tooling, not a product renderer. Chromium renders the original
HTML and Sim2Clip.seekPresentation samples its existing display clock. Recording
CSS fixes only the desktop canvas size. Captions are temporary recording text;
source HTML, result data, event times, and Presentation configuration stay intact.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

SOURCE_SHA = {
    "queue": "4dcbed6d6bba3e7fcb6be2d72f9b617b8792bcdb5d5e227d91f224c4c64f0e1d",
    "server": "7808c90a42789b7857309d358f1175b0e27fbb924592300967636c29b4d376cc",
}
STAGES = [
    ("question", 0),
    ("plan", 4.5),
    ("tool", 7),
    ("verify", 9),
    ("early", 11.5),
    ("middle", 17),
    ("late", 23.5),
    ("reveal", 25),
]
DESKTOP_CSS = """
body:not(.vertical-layout) .presentation-stage {height:1040px;min-height:1040px}
body:not(.vertical-layout) .p-body {height:552px;min-height:552px}
body:not(.vertical-layout) .p-footer {
 position:absolute;left:48px;right:48px;bottom:28px;margin:0
}
"""
CAPTIONS = {
    "question": "AI 实验计划 → MCP → 模拟计算 → 独立核验",
    "tool": "保存的真实 MCP 调用 · 这里是记录回放",
    "verify": "模拟引擎负责计算，独立验证器负责核验",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample(page, display_time: float, scene: str) -> dict:
    page.evaluate("p => Sim2Clip.seekPresentation(p)", display_time)
    state = page.evaluate("Sim2Clip.getPresentationState()")
    caption = CAPTIONS.get(state["phase"])
    if state["phase"] == "simulate" and scene == "server":
        caption = "共用 Agent 编排 · 独立 Server 模型"
    if state["phase"] == "reveal":
        caption = (
            "仅验证等待变化，不含成本与收益"
            if scene == "queue"
            else "有限负载模型观察，不代表生产性能"
        )
    page.evaluate(
        """caption => {
        document.querySelector('.p-brand span').textContent =
            'AI 把问题变成可复现的实验';
        if (caption) document.querySelector('.p-caption').textContent = caption;
    }""",
        caption,
    )
    assert state["mode"] == "presentation"
    assert not state["playing"]
    assert abs(state["presentation_time"] - display_time) < 1e-8
    assert state["reveal_visible"] == (display_time >= 24)
    return state


def record(args) -> None:
    source = args.source.resolve()
    assert sha(source) == SOURCE_SHA[args.scene], "Accepted source HTML changed"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frames = output / "frames"
    frames.mkdir()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(args.browser.resolve()))
        context = browser.new_context(
            viewport={"width": 1440, "height": 2300}, device_scale_factor=1, offline=True
        )
        requests, errors = [], []
        page = context.new_page()
        page.on("request", lambda r: requests.append(r.url))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(source.as_uri())
        page.evaluate("Sim2Clip.ready || null")
        page.add_style_tag(content=DESKTOP_CSS)
        page.evaluate("layout => Sim2Clip.setLayout(layout)", args.layout)
        if args.layout == "vertical":
            page.evaluate("document.querySelector('.presentation-preview').style.width='1080px'")
            page.wait_for_function(
                "document.querySelector('.presentation-preview').clientHeight===1920"
            )
        stage = page.locator("#presentation-preview")
        payload = json.loads(page.locator("#experiment-data").text_content())
        original_payload = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        execution = payload["presentation"]["execution"]
        assert execution["planner_kind"] == "real_llm"
        assert execution["evidence_completeness"] == "complete"
        assert all(
            c["association"] == "verified" and c["status"] == "success" for c in execution["calls"]
        )
        assert {v["origin"] for v in execution["verification"] if v["valid"]} == {"agent", "export"}
        samples = []
        for label, display_time in STAGES:
            state = sample(page, display_time, args.scene)
            text = stage.inner_text()
            if display_time < 24:
                assert not page.locator(".p-results,.p-delta,.p-scope").count()
            else:
                assert "Seed 42" in text
            if label == "tool":
                tool = (
                    "compare_simulations" if args.scene == "queue" else "compare_server_simulations"
                )
                assert tool in text and "已核对当前结果关联" in text
            if label == "verify":
                assert "Agent 当时验证：通过" in text and "导出时复核：通过" in text
            file = output / f"{label}.png"
            stage.screenshot(path=str(file))
            assert Image.open(file).size == (
                (1080, 1920) if args.layout == "vertical" else (1240, 1040)
            )
            # Check rendered DOM/canvas bounds, not just an intended logical size.
            bounds = stage.evaluate("""root => {
                const r=root.getBoundingClientRect();
                const selector='.p-brand,.p-source,.p-question,.p-conditions,'+
                    '.p-body,.p-footer,.stage-card,.stage-canvas';
                return [...root.querySelectorAll(selector)]
                .map(el=>{const b=el.getBoundingClientRect();return {class:el.className,
                    x:b.x-r.x,y:b.y-r.y,width:b.width,height:b.height};});
            }""")
            samples.append(
                {
                    "label": label,
                    "state": state,
                    "text": text,
                    "file": file.name,
                    "sha256": sha(file),
                    "bounds": bounds,
                }
            )
        frame_ledger = []
        for index in range(30 * args.fps):
            display_time = index / args.fps
            state = sample(page, display_time, args.scene)
            path = frames / f"{index:05d}.jpg"
            stage.screenshot(path=str(path), type="jpeg", quality=98)
            frame_ledger.append(
                {
                    "index": index,
                    "display_time": display_time,
                    "phase": state["phase"],
                    "simulation_time": state["simulation_time"],
                    "sha256": sha(path),
                }
            )
            if index % (5 * args.fps) == 0:
                print(
                    f"{args.scene}/{args.layout}: {display_time:g}/30 display seconds", flush=True
                )
        final_payload = json.loads(page.locator("#experiment-data").text_content())
        assert (
            hashlib.sha256(json.dumps(final_payload, sort_keys=True).encode()).hexdigest()
            == original_payload
        )
        assert not errors and all(url == source.as_uri() for url in requests)
        proof = {
            "schema_version": "1",
            "scene": args.scene,
            "layout": args.layout,
            "browser": browser.version,
            "source_html_sha256": sha(source),
            "offline": True,
            "pageerrors": errors,
            "external_requests": 0,
            "display_duration_seconds": 30,
            "capture_fps": args.fps,
            "frames": frame_ledger,
            "samples": samples,
            "recording_only_css": DESKTOP_CSS,
            "recording_only_captions": CAPTIONS,
            "data_payload_unchanged": True,
            "source_unchanged": sha(source) == SOURCE_SHA[args.scene],
        }
        (output / "capture-proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()
    print(f"Captured {len(frame_ledger)} original-stage frames", flush=True)


def encode(args) -> None:
    source = args.capture.resolve()
    proof = json.loads((source / "capture-proof.json").read_text(encoding="utf-8"))
    for frame in proof["frames"]:
        assert sha(source / "frames" / f"{frame['index']:05d}.jpg") == frame["sha256"]
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists(), "Refuse to overwrite an existing media artifact"
    command = [
        str(args.ffmpeg.resolve()),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-n",
        "-framerate",
        str(proof["capture_fps"]),
        "-i",
        str(source / "frames" / "%05d.jpg"),
    ]
    if target.suffix == ".mp4":
        command += [
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
        ]
    elif target.suffix == ".gif":
        # Resample display frames only. Never change source event timestamps.
        graph = (
            f"fps={args.gif_fps},scale={args.width}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=192:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
        )
        command += ["-filter_complex", graph, "-loop", "0"]
    else:
        raise ValueError("Expected .mp4 or .gif")
    command.append(str(target))
    subprocess.run(command, check=True)
    print(json.dumps({"file": target.name, "bytes": target.stat().st_size, "sha256": sha(target)}))


def main() -> None:
    if not __debug__:
        raise RuntimeError("Run without -O: source/evidence checks require assertions")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--scene", choices=["queue", "server"], required=True)
    record_parser.add_argument("--source", type=Path, required=True)
    record_parser.add_argument("--browser", type=Path, required=True)
    record_parser.add_argument("--layout", choices=["desktop", "vertical"], required=True)
    record_parser.add_argument("--fps", type=int, default=20, choices=[10, 20, 25, 30])
    record_parser.add_argument("--output", type=Path, required=True)
    encode_parser = sub.add_parser("encode")
    encode_parser.add_argument("--capture", type=Path, required=True)
    encode_parser.add_argument("--ffmpeg", type=Path, required=True)
    encode_parser.add_argument("--output", type=Path, required=True)
    encode_parser.add_argument("--gif-fps", type=int, default=10)
    encode_parser.add_argument("--width", type=int, default=960)
    args = parser.parse_args()
    record(args) if args.command == "record" else encode(args)


if __name__ == "__main__":
    main()
