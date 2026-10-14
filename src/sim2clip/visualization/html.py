"""Bundle a self-contained, dependency-free replay page, safe for file://."""

import json
from pathlib import Path
from typing import Any

from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.simulation.schemas import ComparisonResult, SimulationResult
from sim2clip.visualization.data import ExperimentResult, parse_result, runs_of
from sim2clip.visualization.presentation import (
    PresentationManifest,
    build_manifest,
    result_reference,
)
from sim2clip.visualization.replay import EventReplay
from sim2clip.visualization.scene import load_design
from sim2clip.visualization.timeline import public_rows


def render_html(
    result: ExperimentResult,
    *,
    context: dict[str, Any] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    presentation: PresentationManifest | None = None,
) -> str:
    result = parse_result(result.model_dump(mode="json"))
    presentation = presentation or build_manifest(result, timeline=timeline)
    if presentation.result_ref != result_reference(result):
        raise ValueError("Presentation references a different result")
    if isinstance(result, (ServerSimulationResult, ServerComparisonResult)):
        return render_server_html(
            result, context=context, timeline=timeline, presentation=presentation
        )
    for run in runs_of(result):
        EventReplay(run)
    assets = Path(__file__).parent / "assets"
    payload = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "context": context or {},
            "agent_timeline": public_rows(timeline or []),
            "presentation": presentation.model_dump(mode="json"),
            "design": load_design(),
            "schemas": {
                "SimulationResult": SimulationResult.model_json_schema(),
                "ComparisonResult": ComparisonResult.model_json_schema(),
            },
        },
        ensure_ascii=False,
        allow_nan=False,
    )

    # HTML parsers recognize </script> even inside JSON strings.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        assets.joinpath("page.html")
        .read_text(encoding="utf-8")
        .replace(
            "/*__STYLE__*/",
            assets.joinpath("style.css").read_text(encoding="utf-8")
            + assets.joinpath("vertical-stage.css").read_text(encoding="utf-8")
            + assets.joinpath("presentation.css").read_text(encoding="utf-8"),
        )
        .replace("__PAYLOAD__", payload)
        .replace(
            "/*__SCRIPT__*/",
            "\n".join(
                assets.joinpath(name).read_text(encoding="utf-8")
                for name in (
                    "replay-core.js",
                    "scene.js",
                    "vertical-stage.js",
                    "queue-vertical.js",
                    "presentation.js",
                    "replay.js",
                )
            ),
        )
    )


def render_server_html(
    result: ServerSimulationResult | ServerComparisonResult,
    *,
    context: dict[str, Any] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    presentation: PresentationManifest | None = None,
) -> str:
    checked = parse_result(result.model_dump(mode="json"))
    if not isinstance(checked, (ServerSimulationResult, ServerComparisonResult)):
        raise ValueError("Expected server evidence")
    result = checked
    presentation = presentation or build_manifest(result, timeline=timeline)
    if presentation.result_ref != result_reference(result):
        raise ValueError("Presentation references a different result")
    assets = Path(__file__).parent / "assets"
    payload = (
        json.dumps(
            {
                "result": result.model_dump(mode="json"),
                "context": context or {},
                "agent_timeline": public_rows(timeline or []),
                "presentation": presentation.model_dump(mode="json"),
                "schemas": {
                    "ServerSimulationResult": ServerSimulationResult.model_json_schema(),
                    "ServerComparisonResult": ServerComparisonResult.model_json_schema(),
                },
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        assets.joinpath("server-page.html")
        .read_text(encoding="utf-8")
        .replace(
            "/*__STYLE__*/",
            assets.joinpath("style.css").read_text(encoding="utf-8")
            + assets.joinpath("server-style.css").read_text(encoding="utf-8")
            + assets.joinpath("vertical-stage.css").read_text(encoding="utf-8")
            + assets.joinpath("presentation.css").read_text(encoding="utf-8"),
        )
        .replace("__PAYLOAD__", payload)
        .replace(
            "/*__SCRIPT__*/",
            "\n".join(
                assets.joinpath(name).read_text(encoding="utf-8")
                for name in (
                    "server-replay.js",
                    "server-scene.js",
                    "vertical-stage.js",
                    "server-vertical.js",
                    "presentation.js",
                    "server-player.js",
                )
            ),
        )
    )
