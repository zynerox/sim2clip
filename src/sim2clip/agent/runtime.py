"""Open durable local workflows and resume by the same thread ID."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from sim2clip.agent.artifacts import ArtifactStore
from sim2clip.agent.checkpoint_compat import compatible_state
from sim2clip.agent.graph import build_graph
from sim2clip.agent.mcp_client import SimulationMCPClient
from sim2clip.agent.nodes import AgentNodes
from sim2clip.agent.planner import Planner
from sim2clip.agent.policy import AgentPolicy
from sim2clip.agent.state import SimulationAgentState, initial_state


class AgentWorkflow:
    def __init__(
        self,
        graph: CompiledStateGraph[
            SimulationAgentState, None, SimulationAgentState, SimulationAgentState
        ],
        policy: AgentPolicy,
        planner_mode: str = "",
        *,
        verify_completed: Callable[[SimulationAgentState], None] | None = None,
    ) -> None:
        self.graph, self.policy = graph, policy
        self.planner_mode = planner_mode
        self.verify_completed = verify_completed

    def config(self, thread_id: str) -> RunnableConfig:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", thread_id):
            raise ValueError("thread_id must contain 1..80 letters, digits, underscores or hyphens")
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

    async def run(self, query: str, *, thread_id: str) -> SimulationAgentState:
        if not query.strip():
            raise ValueError("Query cannot be blank")
        config = self.config(thread_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            raise ValueError("Thread already exists; use resume() or a new thread_id")
        state = initial_state(query, thread_id, self.policy.to_dict())
        state["planner_mode"] = self.planner_mode
        return cast(
            SimulationAgentState, await self.graph.ainvoke(state, config, durability="sync")
        )

    async def resume(self, *, thread_id: str) -> SimulationAgentState:
        config = self.config(thread_id)
        snapshot = await self.graph.aget_state(config)
        if not snapshot.values:
            raise ValueError("No checkpoint for this thread_id")
        if not snapshot.next:
            state = compatible_state(cast(SimulationAgentState, snapshot.values))
            if state["status"] == "completed" and self.verify_completed is not None:
                self.verify_completed(state)
            return state
        # Nodes project missing fields at entry and persist them with their next
        # normal update. No synthetic input, lost pending route or history rewrite.
        compatible_state(cast(SimulationAgentState, snapshot.values))
        return compatible_state(
            cast(SimulationAgentState, await self.graph.ainvoke(None, config, durability="sync"))
        )


@asynccontextmanager
async def open_workflow(
    planner: Planner,
    *,
    checkpoint_path: Path,
    artifact_root: Path,
    policy: AgentPolicy | None = None,
    server: Any = None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
) -> AsyncIterator[AgentWorkflow]:
    policy = policy or AgentPolicy()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        await saver.setup()
        async with SimulationMCPClient(server) as mcp:
            nodes = AgentNodes(planner, mcp, ArtifactStore(artifact_root))
            graph = build_graph(
                nodes, saver, interrupt_before=interrupt_before, interrupt_after=interrupt_after
            )
            yield AgentWorkflow(
                graph, policy, planner.mode, verify_completed=nodes.verify_completed
            )
