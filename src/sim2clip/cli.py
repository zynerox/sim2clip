"""Product CLI: configuration and presentation around the durable workflow."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import cast
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from mcp.types import Tool

from sim2clip import __version__
from sim2clip.agent import DemoPlanner, Planner, open_workflow
from sim2clip.agent.artifacts import ArtifactStore, json_bytes
from sim2clip.agent.planner import PlannerFailure
from sim2clip.agent.public_errors import public_errors, task_output
from sim2clip.agent.state import JSONDict, SimulationAgentState
from sim2clip.config import ConfigurationError, ModelSettings, create_model_planner
from sim2clip.visualization.data import (
    ExperimentResult,
    VisualizationError,
    parse_result,
    scenario_id_of,
)
from sim2clip.visualization.pipeline import (
    atomic_write,
    export_capabilities,
    export_demo,
    json_data,
)
from sim2clip.visualization.public import public_context, public_report, public_state
from sim2clip.visualization.timeline import execution_timeline


class LazyModelPlanner:
    """Resume validated work without needing credentials until another model turn."""

    mode = "llm"

    def __init__(self, env_file: Path | None, timeout: float) -> None:
        self.env_file, self.timeout = env_file, timeout
        self.planner: Planner | None = None

    async def plan(self, state: SimulationAgentState, tools: list[Tool]) -> AIMessage:
        if self.planner is None:
            try:
                self.planner = create_model_planner(
                    ModelSettings.load(self.env_file), timeout=self.timeout
                )
            except ConfigurationError as exc:
                raise PlannerFailure("MODEL_CONFIGURATION_ERROR", str(exc)) from None
        return await self.planner.plan(state, tools)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sim2clip", description="可验证、可恢复的多场景实验 Agent")
    root.add_argument("--version", action="version", version=f"Sim2Clip {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="通过 Agent 规划并执行实验")
    run.add_argument("query", help="自然语言实验问题")
    run.add_argument("--thread-id", help="任务 ID；省略时自动生成")
    resume = commands.add_parser("resume", help="恢复 SQLite 中的已有任务")
    resume.add_argument("thread_id", help="原任务 ID")
    for command in (run, resume):
        command.add_argument(
            "--planner",
            choices=("demo", "model"),
            default=None,
            help="新任务默认 model；恢复任务默认沿用原规划器",
        )
        command.add_argument(
            "--output",
            type=Path,
            default=Path("outputs"),
            help="checkpoint 与结果根目录",
        )
        command.add_argument("--env-file", type=Path, help="指定 .env 文件，默认当前目录 .env")
        command.add_argument(
            "--planner-timeout", type=float, default=60.0, help="单次规划超时秒数，默认 60"
        )
        command.add_argument(
            "--pause-after-tool", action="store_true", help="在工具调用保存后暂停，供检查与恢复"
        )
        command.add_argument("--json", action="store_true", help="仅向 stdout 输出 JSON 摘要")
        command.add_argument(
            "--skip-visualization", action="store_true", help="只执行实验，跳过 HTML / GIF 导出"
        )
    visualize = commands.add_parser("visualize", help="从已有结果重新生成 HTML 与 GIF，不执行实验")
    visualize.add_argument(
        "result", type=Path, help="完整 SimulationResult / ComparisonResult JSON"
    )
    visualize.add_argument("--output", type=Path, required=True, help="展示产物目录")
    visualize.add_argument("--state", type=Path, help="该实验的 Agent state.json，可选")
    visualize.add_argument("--checkpoint", type=Path, help="原 SQLite 文件，提供真实节点完成时间")
    visualize.add_argument("--json", action="store_true", help="仅向 stdout 输出 JSON 摘要")
    return root


def valid_thread_id(thread_id: str) -> None:
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", thread_id):
        raise ConfigurationError("任务 ID 只能包含 1–80 个字母、数字、下划线或连字符")


async def read_checkpoint(path: Path, thread_id: str) -> SimulationAgentState:
    if not path.is_file():
        raise ConfigurationError("未找到 checkpoint 文件，请使用原来的 --output")
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
        if checkpoint is None:
            raise ConfigurationError("未找到该任务的 checkpoint")
        return cast(SimulationAgentState, checkpoint.checkpoint["channel_values"])


async def execute(args: argparse.Namespace) -> tuple[int, JSONDict, str]:
    if args.command == "visualize":
        return await visualize_existing(args)
    output: Path = args.output.resolve()
    thread_id: str = args.thread_id or f"run-{uuid4().hex}"
    valid_thread_id(thread_id)
    if args.command == "run" and any(
        path.exists() for path in (output / thread_id, output / "runs" / thread_id)
    ):
        raise ConfigurationError(
            "Task output directory already exists; use resume or a new task ID"
        )
    if not 0 < args.planner_timeout <= 3600:
        raise ConfigurationError("--planner-timeout 必须大于 0 且不超过 3600 秒")
    checkpoint_path = output / "checkpoints.sqlite"
    saved: SimulationAgentState | None = None
    if args.command == "resume":
        saved = await read_checkpoint(checkpoint_path, thread_id)
        original = {"llm": "model", "offline_demo": "demo"}.get(saved["planner_mode"])
        if original is None:
            raise ConfigurationError("checkpoint 没有可恢复的产品规划器模式")
        if args.planner is not None and args.planner != original:
            raise ConfigurationError("恢复时不能切换规划器；请为新实验创建新任务")
        mode = original
    else:
        if not args.query.strip():
            raise ConfigurationError("实验问题不能为空")
        mode = args.planner or "model"
    planner: Planner
    if mode == "demo":
        planner = DemoPlanner()
    elif saved is not None:
        planner = LazyModelPlanner(args.env_file, args.planner_timeout)
    else:
        planner = create_model_planner(
            ModelSettings.load(args.env_file), timeout=args.planner_timeout
        )
    if not args.json:
        print(f"任务 ID：{thread_id}；规划器：{mode}", flush=True)
    async with open_workflow(
        planner,
        checkpoint_path=checkpoint_path,
        artifact_root=output / "runs",
        interrupt_after=["executor"] if args.pause_after_tool else None,
    ) as workflow:
        try:
            state = (
                await workflow.resume(thread_id=thread_id)
                if saved is not None
                else await workflow.run(args.query, thread_id=thread_id)
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from None
        snapshot = await workflow.graph.aget_state(workflow.config(thread_id))
        store = ArtifactStore(output / "runs")
        directory = store.run_dir(thread_id)
        store.write(directory / "state.json", json_bytes(state))
        store.write(
            directory / "graph.mmd", workflow.graph.get_graph().draw_mermaid().encode("utf-8")
        )
        paused = bool(snapshot.next)
        presentation: dict[str, str] = {}
        visualization_supported = state.get("scenario_id") in (
            None,
            "queue",
            "server_load_balancing",
        )
        capabilities: JSONDict = {"supported": visualization_supported, "note": ""}
        published_report = ""
        result: ExperimentResult | None = None
        if not paused and state["status"] == "completed":
            reference = state["comparison_result"] or state["simulation_results"][-1]
            result = parse_result(store.load(reference))
            if scenario_id_of(result) != (state.get("scenario_id") or "queue"):
                raise VisualizationError("Validated State scenario does not match result evidence")
            published_report = public_report(result, dict(state))
        if (
            not paused
            and state["status"] == "completed"
            and not args.skip_visualization
            and visualization_supported
        ):
            assert result is not None
            history = [
                {
                    "timestamp": item.created_at,
                    "step": (item.metadata or {}).get("step"),
                    "state": {
                        key: item.values.get(key, []) for key in ("decision_history", "tool_calls")
                    },
                }
                async for item in workflow.graph.aget_state_history(workflow.config(thread_id))
            ]
            capabilities = export_capabilities(result)
            from sim2clip.visualization.presentation import build_manifest

            narrative_timeline = execution_timeline(list(reversed(history)), dict(state))
            presentation = await asyncio.to_thread(
                export_demo,
                result,
                output / thread_id,
                report=published_report,
                context={
                    **public_context(dict(state)),
                    "selection_note": (
                        "本次回放显示最后一个独立模拟；其他结果保存在 results/ 中。"
                        if state["comparison_result"] is None
                        and len(state["simulation_results"]) > 1
                        else ""
                    ),
                },
                timeline=narrative_timeline,
                presentation=build_manifest(
                    result, timeline=narrative_timeline, state=dict(state), store=store
                ),
            )
            # Preserve every independent single-tool result without inventing a paired comparison.
            if state["comparison_result"] is None and len(state["simulation_results"]) > 1:
                for item in state["simulation_results"]:
                    atomic_write(
                        output / thread_id / "results" / f"{item['result_id']}.json",
                        json_data(store.load(item)),
                    )
            for name in ("graph.mmd",):
                source = directory / name
                if source.is_file():
                    atomic_write(output / thread_id / name, source.read_bytes())
            presentation["graph"] = str(output / thread_id / "graph.mmd")
        if not args.skip_visualization:
            published_state = public_state(dict(state), published_report if presentation else "")
            atomic_write(output / thread_id / "state.json", json_data(published_state))
            atomic_write(
                output / thread_id / "decisions.json",
                json_data(published_state["decision_history"]),
            )
            presentation["state"] = str(output / thread_id / "state.json")
            presentation["decisions"] = str(output / thread_id / "decisions.json")
        summary = {
            "thread_id": thread_id,
            "status": "paused" if paused else state["status"],
            "state_status": state["status"],
            "planner": mode,
            "experiment_goal": public_context(dict(state))["experiment_goal"]
            if state["status"] == "completed"
            else None,
            "scenario_id": state.get("scenario_id"),
            "schema_version": state.get("schema_version", "1"),
            "visualization": capabilities,
            "planning_rounds": state["planning_round"],
            "tool_attempts": len(state["tool_calls"]),
            "retry_counts": state["retry_counts"],
            "next_nodes": list(snapshot.next),
            "checkpoint": str(checkpoint_path),
            "artifacts": {
                **state["artifacts"],
                "state": str(directory / "state.json"),
                "graph": str(directory / "graph.mmd"),
                **presentation,
            },
            "output_directory": str(output / thread_id if presentation else directory),
            "errors": public_errors(state["errors"], feedback=state["feedback"]),
        }
        return (
            (1 if state["status"] == "failed" else 0),
            summary,
            published_report,
        )


async def visualize_existing(args: argparse.Namespace) -> tuple[int, JSONDict, str]:
    try:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise VisualizationError("Result file is not valid JSON") from None
    result = parse_result(payload)
    state: JSONDict = {}
    history: list[JSONDict] = []
    if args.state is not None:
        try:
            state = json.loads(args.state.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise VisualizationError("Agent state file is not valid JSON") from None
        if not isinstance(state, dict):
            raise VisualizationError("Agent state file must contain a JSON object")
        expected = result.model_dump().get("comparison_id") or result.model_dump()["result_id"]
        identities = {item["result_id"] for item in state.get("simulation_results", [])}
        if state.get("comparison_result"):
            identities.add(state["comparison_result"]["comparison_id"])
        if expected not in identities:
            raise VisualizationError("Agent state belongs to a different experiment result")
        if (state.get("scenario_id") or "queue") != scenario_id_of(result):
            raise VisualizationError("Agent state scenario does not match result evidence")
        if state.get("schema_version", "1") != "1":
            raise VisualizationError("Agent state schema version does not match result evidence")
    if args.checkpoint is not None:
        if not state or not args.checkpoint.is_file():
            raise VisualizationError("--checkpoint requires an existing file and matching --state")
        async with AsyncSqliteSaver.from_conn_string(str(args.checkpoint)) as saver:
            history = [
                {
                    "timestamp": item.checkpoint["ts"],
                    "step": item.metadata.get("step"),
                    "state": item.checkpoint["channel_values"],
                }
                async for item in saver.alist({"configurable": {"thread_id": state["run_id"]}})
            ]
        if not history:
            raise VisualizationError("No matching Agent checkpoints")
    report = (
        public_report(result, {**state, "scenario_id": scenario_id_of(result)}) if state else ""
    )
    if not report and payload.get("scenario") != "server_load_balancing":
        report = "# Sim2Clip 事件回放\n\n从已有结果生成；原始文件未提供 Agent 报告。\n"
    from sim2clip.visualization.presentation import build_manifest

    narrative_timeline = execution_timeline(list(reversed(history)), state)
    artifacts = await asyncio.to_thread(
        export_demo,
        result,
        args.output,
        report=report,
        context=public_context(state) if state else {},
        timeline=narrative_timeline,
        presentation=build_manifest(result, timeline=narrative_timeline, state=state),
    )
    return (
        0,
        {
            "status": "completed",
            "visualization": export_capabilities(result),
            "artifacts": artifacts,
            "output_directory": str(args.output.resolve()),
            "errors": [],
        },
        "",
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    if args.command == "run" and args.thread_id is None:
        args.thread_id = f"run-{uuid4().hex}"
    try:
        code, summary, report = asyncio.run(execute(args))
    except KeyboardInterrupt:
        interrupted: JSONDict = {"status": "interrupted"}
        interrupted_id = getattr(args, "thread_id", None)
        if interrupted_id is not None:
            interrupted.update(
                thread_id=interrupted_id,
                checkpoint=str(args.output.resolve() / "checkpoints.sqlite"),
            )
        if args.json:
            print(json.dumps(interrupted, ensure_ascii=False))
        else:
            print(
                f"任务 {interrupted_id} 已中断；若有 checkpoint，可用同一 ID 恢复。"
                if interrupted_id
                else "展示导出已中断，可以从原始结果重新导出。",
                file=sys.stderr,
            )
        return 130
    except (ConfigurationError, VisualizationError, OSError) as exc:
        summary = {
            "status": "failed",
            "errors": [
                {
                    "code": "CONFIGURATION_ERROR"
                    if isinstance(exc, ConfigurationError)
                    else "VISUALIZATION_ERROR"
                    if isinstance(exc, VisualizationError)
                    else "IO_ERROR",
                    "message": str(exc),
                }
            ],
        }
        code, report = 2, ""
    except Exception as exc:
        summary = {
            "status": "failed",
            "errors": [
                {
                    "code": "RUNTIME_ERROR",
                    "message": (f"Workflow runtime failed ({type(exc).__name__})"),
                }
            ],
        }
        code, report = 1, ""
    if summary["status"] == "failed" and args.command in {"run", "resume"}:
        summary.update(
            thread_id=args.thread_id, checkpoint=str(args.output.resolve() / "checkpoints.sqlite")
        )
    summary["errors"] = public_errors(summary["errors"])
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    else:
        stdout, stderr = task_output(summary["status"], report, summary["errors"])
        print(stdout, end="")
        print(stderr, end="", file=sys.stderr)
        if summary.get("artifacts"):
            print(f"产物目录：{summary['output_directory']}")
        if summary["status"] == "paused":
            print(f'恢复：sim2clip resume {summary["thread_id"]} --output "{args.output}"')
    return code


def entrypoint() -> None:
    raise SystemExit(main())
