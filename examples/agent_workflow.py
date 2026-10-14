"""Run and resume an Agent workflow using a real subprocess MCP connection."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sim2clip.agent import DemoPlanner, Planner, open_workflow
from sim2clip.config import ModelSettings, create_model_planner


def choose_planner(mode: str) -> Planner:
    if mode == "demo":
        return DemoPlanner()
    return create_model_planner(ModelSettings.load())


async def execute(args: argparse.Namespace) -> int:
    root = args.output.resolve()
    async with open_workflow(
        choose_planner(args.planner),
        checkpoint_path=root / "checkpoints.sqlite",
        artifact_root=root / "runs",
        interrupt_after=["executor"] if args.pause_after_tool else None,
    ) as workflow:
        if args.resume:
            state = await workflow.resume(thread_id=args.thread_id)
        else:
            state = await workflow.run(args.query, thread_id=args.thread_id)
        snapshot = await workflow.graph.aget_state(workflow.config(args.thread_id))
        directory = root / "runs" / args.thread_id
        directory.mkdir(parents=True, exist_ok=True)
        # This inspection snapshot is convenient; SQLite is the source of recovery.
        (directory / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        (directory / "graph.mmd").write_text(
            workflow.graph.get_graph().draw_mermaid(), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": state["status"],
                    "planner_mode": state["planner_mode"],
                    "tool_attempts": len(state["tool_calls"]),
                    "planning_rounds": state["planning_round"],
                    "next_nodes": list(snapshot.next),
                    "artifacts": state["artifacts"],
                    "errors": state["errors"],
                },
                ensure_ascii=False,
            )
        )
        if state["report"]:
            print(state["report"])
        return 1 if state["status"] == "failed" else 0


def main() -> None:
    # Match saved UTF-8 artifacts when PowerShell redirects Chinese report text.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="100个顾客，比较1个收银台和3个收银台")
    parser.add_argument("--planner", choices=("demo", "llm"), default="demo")
    parser.add_argument("--thread-id", default="convenience-store")
    parser.add_argument("--output", type=Path, default=Path("outputs/agent-demo"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pause-after-tool", action="store_true")
    args = parser.parse_args()
    try:
        code = asyncio.run(execute(args))
    except (ValueError, ImportError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
