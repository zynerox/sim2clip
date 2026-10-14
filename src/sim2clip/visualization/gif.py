"""Portable Pillow storyboard driven by the same event replay as evidence checks."""

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from sim2clip.simulation.schemas import SimulationResult
from sim2clip.visualization.data import QueueResult, select_demo_runs
from sim2clip.visualization.replay import EventReplay, ReplaySnapshot

WIDTH, HEIGHT = 1200, 720
INK, MUTED = "#173b36", "#677972"
GREEN, AMBER, BLUE = "#117763", "#e99b34", "#478bb4"
STATE_COLORS = {"not_arrived": "#dce3da", "waiting": AMBER, "serving": BLUE, "completed": GREEN}


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        Path(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def frame_schedule(end_time: float, frames: int = 121) -> list[tuple[float, int]]:
    if not 2 <= frames <= 301:
        raise ValueError("GIF frame count must be between 2 and 301")
    return [
        (
            end_time * index / (frames - 1),
            1200 if index == 0 else 3000 if index == frames - 1 else 100,
        )
        for index in range(frames)
    ]


def draw_frame(
    runs: tuple[SimulationResult, ...],
    snapshots: tuple[ReplaySnapshot, ...],
    timestamp: float,
    end_time: float,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f5f6ef")
    draw = ImageDraw.Draw(image)

    def text(
        x: float, y: float, value: str, size: int = 14, color: str = INK, bold: bool = False
    ) -> None:
        draw.text((x, y), value, font=font(size, bold), fill=color)

    def person(x: float, y: float, color: str, scale: float = 1) -> None:
        draw.ellipse((x - 3 * scale, y - 7 * scale, x + 3 * scale, y - scale), fill=color)
        draw.rounded_rectangle(
            (x - 3 * scale, y + scale, x + 3 * scale, y + 10 * scale), radius=2 * scale, fill=color
        )

    seed, customers = runs[0].config.seed, runs[0].config.customers
    text(38, 22, "Sim2Clip", 23, bold=True)
    text(950, 29, "EVENT REPLAY / SIMPY", 12, GREEN, True)
    draw.line((38, 64, 1162, 64), fill="#dfe5dd")
    text(38, 81, "CONVENIENCE STORE RUSH", 11, GREEN, True)
    text(38, 103, f"Same {customers} customers. How much does capacity change?", 29, bold=True)
    text(
        38,
        145,
        f"Paired customer samples  /  FIFO queue  /  seed {seed}  /  time in seconds",
        14,
        MUTED,
    )
    draw.rounded_rectangle((38, 180, 1162, 243), 10, fill=INK)
    text(58, 191, "SHARED SIMULATION CLOCK", 10, "#b4cfc2", True)
    text(58, 206, f"{int(timestamp // 60):02d}:{timestamp % 60:04.1f}", 23, "#ffffff", True)
    draw.rounded_rectangle((284, 209, 1005, 216), 3, fill="#43645c")
    if timestamp > 0:
        draw.rounded_rectangle((284, 209, 284 + 721 * timestamp / end_time, 216), 3, fill="#e4f6a6")
    text(1030, 203, f"{timestamp:.0f} / {end_time:.0f}s", 13, "#ffffff")
    gap, left, available = 22, 38, 1124
    panel_width = (available - gap * (len(runs) - 1)) / len(runs)
    for index, (run, snapshot) in enumerate(zip(runs, snapshots, strict=True)):
        x, y, w = left + index * (panel_width + gap), 261, panel_width
        draw.rounded_rectangle((x, y, x + w, 631), 12, fill="#ffffff", outline="#dfe5dd")
        accent = GREEN if index else AMBER
        draw.rounded_rectangle(
            (x + 20, y + 18, x + 47, y + 45), 5, fill="#e7f2ec" if index else "#fbedd8"
        )
        text(x + 28, y + 22, "B" if index else "A", 14, accent, True)
        text(
            x + 59,
            y + 18,
            f"{run.config.cashiers} cashier" + ("s" if run.config.cashiers > 1 else ""),
            22,
            bold=True,
        )
        done = snapshot.completed == customers
        text(x + w - 127, y + 26, "ALL DONE" if done else "REPLAYING", 11, GREEN, True)
        text(
            x + 20,
            y + 56,
            f"Arrival mean {run.config.mean_interarrival_time:g}s  /  "
            f"service mean {run.config.mean_service_time:g}s",
            12,
            MUTED,
        )
        values = [
            ("WAITING", str(len(snapshot.queue)), AMBER),
            ("COMPLETED", str(snapshot.completed), GREEN),
            (
                "BUSY CASHIERS",
                f"{sum(c is not None for c in snapshot.cashiers.values())}/{run.config.cashiers}",
                BLUE,
            ),
        ]
        for column, (label, value, color) in enumerate(values):
            cx = x + 20 + column * (w - 40) / 3
            text(cx, y + 82, label, 10, MUTED, True)
            text(cx, y + 99, value, 30, color, True)
        draw.rounded_rectangle((x + 20, y + 146, x + w - 20, y + 236), 6, fill="#f4f3e9")
        text(x + 31, y + 155, "SHARED FIFO QUEUE", 10, MUTED)
        visible = min(36, len(snapshot.queue))
        for k in range(visible):
            person(x + 35 + (k % 18) * (w - 70) / 18, y + 187 + (k // 18) * 26, AMBER, 0.9)
        if not snapshot.queue:
            text(x + 31, y + 188, "Queue clear", 14, GREEN)
        elif len(snapshot.queue) > visible:
            text(x + w - 120, y + 217, f"+ {len(snapshot.queue) - visible} more", 10, MUTED)
        slots = min(run.config.cashiers, 6)
        slot_width = (w - 40) / slots
        for k in range(slots):
            sx = x + 20 + k * slot_width
            busy = snapshot.cashiers[k + 1] is not None
            draw.rounded_rectangle(
                (sx + 2, y + 246, sx + slot_width - 2, y + 285),
                4,
                fill="#e3f0f6" if busy else "#edf4ed",
            )
            text(
                sx + 10,
                y + 253,
                f"#{k + 1} " + ("BUSY" if busy else "IDLE"),
                10,
                BLUE if busy else MUTED,
            )
            if busy:
                person(sx + slot_width - 22, y + 267, BLUE, 0.85)
        if run.config.cashiers > 6:
            text(
                x + 20, y + 288, "First 6 counters shown; counts include every counter.", 10, MUTED
            )
        ids = [s.customer_id for s in run.customer_samples[:100]]
        for k, customer in enumerate(ids):
            person(
                x + 24 + (k % 50) * (w - 47) / 50,
                y + 302 + (k // 50) * 14,
                STATE_COLORS[snapshot.customer_states[customer]],
                0.4,
            )
        draw.line((x + 20, y + 332, x + w - 20, y + 332), fill="#dfe5dd")
        text(
            x + 20,
            y + 344,
            f"Full-run mean wait  {run.metrics.average_wait_time:.2f}s",
            13,
            bold=True,
        )
        text(x + w - 126, y + 345, f"Peak queue {run.metrics.max_queue_length}", 11, MUTED)
    labels = [
        ("Not arrived", "#dce3da"),
        ("Waiting", AMBER),
        ("Checking out", BLUE),
        ("Completed", GREEN),
    ]
    for index, (label, color) in enumerate(labels):
        x = 40 + index * 145
        draw.ellipse((x, 650, x + 7, 657), fill=color)
        text(x + 15, 647, label, 11, MUTED)
    text(710, 647, "State changes only when an event occurs.", 11, MUTED)
    text(
        38,
        685,
        f"Single-seed replay ({seed}). Multi-seed summaries are in the report and HTML."
        + (f" Customer markers: first 100 of {customers}." if customers > 100 else ""),
        11,
        MUTED,
    )
    return image


def render_gif(
    result: QueueResult, *, result_sha256: str, frames: int = 121
) -> tuple[bytes, dict[str, Any]]:
    runs = select_demo_runs(result)
    replays = [EventReplay(run) for run in runs]
    end_time = max(run.metrics.simulation_end_time for run in runs)
    schedule = frame_schedule(end_time, frames)
    images: list[Image.Image] = []
    ledger: list[dict[str, Any]] = []
    preview_time = end_time / 2
    preview = draw_frame(
        runs, tuple(replay.seek(preview_time) for replay in replays), preview_time, end_time
    )
    # One palette prevents flicker; include the progress color absent at time zero.
    palette = preview.quantize(colors=256)
    for timestamp, duration in schedule:
        snapshots = tuple(replay.seek(timestamp) for replay in replays)
        frame = draw_frame(runs, snapshots, timestamp, end_time)
        images.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
        ledger.append(
            {
                "simulation_time": timestamp,
                "duration_ms": duration,
                "runs": [
                    {
                        "result_id": run.result_id,
                        "events_applied": snapshot.events_applied,
                        "queue_length": len(snapshot.queue),
                        "completed": snapshot.completed,
                        "busy_cashiers": sum(c is not None for c in snapshot.cashiers.values()),
                    }
                    for run, snapshot in zip(runs, snapshots, strict=True)
                ],
            }
        )
    manifest = {
        "renderer_version": "1",
        "seed": runs[0].config.seed,
        "result_sha256": result_sha256,
        "width": WIDTH,
        "height": HEIGHT,
        "duration_ms": sum(d for _, d in schedule),
        "frames": ledger,
    }
    stream = io.BytesIO()
    images[0].save(
        stream,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=[d for _, d in schedule],
        loop=0,
        disposal=1,
        optimize=False,
        comment=json.dumps(
            {"seed": manifest["seed"], "result_sha256": result_sha256}, sort_keys=True
        ).encode(),
    )
    content = stream.getvalue()
    manifest["gif_sha256"] = hashlib.sha256(content).hexdigest()
    return content, manifest
