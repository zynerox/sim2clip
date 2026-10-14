"""A process driver; package revision is determined by isolated PYTHONPATH."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.client.stdio import StdioServerParameters

import sim2clip
from sim2clip.agent import DemoPlanner, open_workflow
from sim2clip.agent.state import SimulationAgentState


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--boundary", default="complete")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        assert "scenario_id" not in SimulationAgentState.__annotations__
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sim2clip.mcp_server"],
        env={"PYTHONPATH": str(Path(sim2clip.__file__).resolve().parents[1])},
    )
    async with open_workflow(
        DemoPlanner(),
        server=server,
        checkpoint_path=args.root / "checkpoints.sqlite",
        artifact_root=args.root / "runs",
        interrupt_before=None if args.resume or args.boundary == "complete" else [args.boundary],
    ) as workflow:
        state = (
            await workflow.resume(thread_id="legacy")
            if args.resume
            else await workflow.run("100个顾客，比较1个收银台和3个收银台", thread_id="legacy")
        )
        snapshot = await workflow.graph.aget_state(workflow.config("legacy"))
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / ("new-state.json" if args.resume else "old-state.json")).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": state["status"],
                "next": list(snapshot.next),
                "package_source": __import__("sim2clip").__file__,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
