"""Export presentation artifacts from existing evidence, without tool execution."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.visualization.data import ExperimentResult, VisualizationError, parse_result
from sim2clip.visualization.gif import render_gif
from sim2clip.visualization.html import render_html
from sim2clip.visualization.presentation import PresentationManifest, build_manifest
from sim2clip.visualization.server_report import saved_server_report
from sim2clip.visualization.timeline import public_rows


def json_data(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode(
        "utf-8"
    )


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(directory: Path, outputs: dict[str, tuple[str, bytes]]) -> None:
    """Stage the entire bundle; roll back owned files on a publication failure."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    for name, _ in outputs.values():
        target = directory / name
        if target.is_symlink() or not target.resolve().is_relative_to(directory):
            raise VisualizationError("Output file resolves outside the selected directory")
        if target.exists() and not target.is_file():
            raise VisualizationError("Output filename is occupied by a non-file")
    with tempfile.TemporaryDirectory(prefix=".sim2clip-export-", dir=directory.parent) as staging:
        stage = Path(staging)
        bundle = stage / "bundle"
        bundle.mkdir()
        for name, data in outputs.values():
            atomic_write(bundle / name, data)
        if not directory.exists():
            bundle.replace(directory)
            return
        backups = {
            name: (directory / name).read_bytes()
            for name, _ in outputs.values()
            if (directory / name).is_file()
        }
        published: list[str] = []
        try:
            for name, _ in outputs.values():
                (bundle / name).replace(directory / name)
                published.append(name)
        except BaseException:
            for name in reversed(published):
                if name in backups:
                    atomic_write(directory / name, backups[name])
                else:
                    (directory / name).unlink()
            raise


def export_demo(
    result: ExperimentResult,
    directory: Path,
    *,
    report: str = "",
    context: dict[str, Any] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    presentation: PresentationManifest | None = None,
    frames: int = 121,
) -> dict[str, str]:
    """Validate and render before publishing files. No random seeds or fabricated events."""
    directory = directory.resolve()
    timeline = public_rows(timeline or [])
    result = parse_result(result.model_dump(mode="json"))
    if not report and isinstance(result, (ServerSimulationResult, ServerComparisonResult)):
        report = saved_server_report(result)
    value = result.model_dump(mode="json")
    content = json_data(value)
    metadata = {**(context or {}), "report": report}
    presentation = presentation or build_manifest(result, timeline=timeline)
    html = render_html(result, context=metadata, timeline=timeline, presentation=presentation)
    outputs = {
        "report": ("report.md", report.encode("utf-8")),
        "result": ("result.json", content),
        "visualization": ("visualization.html", html.encode("utf-8")),
        "timeline": ("agent-timeline.json", json_data(timeline or [])),
        "presentation": ("presentation.json", json_data(presentation.model_dump(mode="json"))),
    }
    if not isinstance(result, (ServerSimulationResult, ServerComparisonResult)):
        try:
            gif, manifest = render_gif(
                result, result_sha256=hashlib.sha256(content).hexdigest(), frames=frames
            )
        except Exception as exc:
            raise VisualizationError(f"Queue GIF generation failed: {exc}") from exc
        outputs["gif"] = ("demo.gif", gif)
        outputs["gif_manifest"] = ("demo.frames.json", json_data(manifest))
    # Never overwrite an unrelated experiment or leave an old queue GIF next to server evidence.
    existing = directory / "result.json"
    if existing.is_file() and existing.read_bytes() != content:
        raise VisualizationError(
            "Output directory contains a different result; choose a new directory"
        )
    if isinstance(result, (ServerSimulationResult, ServerComparisonResult)) and any(
        (directory / name).exists() for name in ("demo.gif", "demo.frames.json")
    ):
        raise VisualizationError("Server output directory contains unrelated GIF artifacts")
    if not existing.exists() and any((directory / name).exists() for name, _ in outputs.values()):
        raise VisualizationError("Output directory contains user files with reserved export names")
    publish(directory, outputs)
    return {key: str(directory / name) for key, (name, _) in outputs.items()}


def export_capabilities(result: ExperimentResult) -> dict[str, Any]:
    server = isinstance(result, (ServerSimulationResult, ServerComparisonResult))
    return {
        "supported": True,
        "note": "",
        "gif": {
            "status": "unsupported" if server else "supported",
            "reason": "服务器场景当前不支持 GIF 导出。" if server else "",
        },
    }
