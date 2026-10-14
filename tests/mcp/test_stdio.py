"""Launch real child processes and exercise the stdio MCP protocol."""

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

from sim2clip.simulation import (
    ComparisonResult,
    SimulationConfig,
    SimulationResult,
    run_simulation,
)

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def subprocess_client(
    *, cwd: Path | None = None, script: Path | None = None
) -> AsyncIterator[Client]:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(script)] if script is not None else ["-m", "sim2clip.mcp_server"],
        cwd=cwd,
    )
    async with Client(params, read_timeout_seconds=30.0) as client:
        yield client


async def test_real_stdio_discovery_and_official_demo(tmp_path):
    # Working from another directory proves this is an installed module, not
    # an accidental import from the repository working directory.
    async with subprocess_client(cwd=tmp_path) as client:
        assert client.server_info.name == "sim2clip-simulation"
        assert client.protocol_version
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {
            "run_simulation",
            "compare_simulations",
            "run_server_simulation",
            "compare_server_simulations",
        }
        run = await client.call_tool(
            "run_simulation", {"cashiers": 3, "customers": 100, "seed": 42}
        )
        comparison = await client.call_tool(
            "compare_simulations",
            {"experiments": [{"cashiers": 1}, {"cashiers": 3}], "seeds": [42, 43, 44, 45, 46]},
        )
        assert not run.is_error and not comparison.is_error
        actual = SimulationResult.model_validate(run.structured_content)
        assert actual == run_simulation(SimulationConfig(cashiers=3, customers=100, seed=42))
        compared = ComparisonResult.model_validate(comparison.structured_content)
        assert len(compared.results) == 10
        assert all(result.metrics.completed_customers == 100 for result in compared.results)
        assert sum(len(result.events) for result in compared.results) == 3000
        assert isinstance(comparison.content[0], TextContent)
        assert json.loads(comparison.content[0].text) == comparison.structured_content
        # Repeating a deterministic request gives the same simulation identity.
        repeated = await client.call_tool(
            "run_simulation", {"cashiers": 3, "customers": 100, "seed": 42}
        )
        assert repeated.structured_content == run.structured_content


async def test_stdio_parameter_error_does_not_kill_the_session():
    async with subprocess_client() as client:
        bad = await client.call_tool(
            "run_simulation", {"cashiers": 1, "customers": -100, "typo": 1}
        )
        assert bad.is_error
        assert bad.structured_content["error"]["code"] == "INVALID_PARAMETERS"
        assert ["typo"] in [
            issue["location"] for issue in bad.structured_content["error"]["issues"]
        ]
        good = await client.call_tool("run_simulation", {"cashiers": 1, "customers": 2})
        assert not good.is_error


async def test_engine_failures_cross_stdio_as_tool_errors_and_server_recovers():
    fixture = Path(__file__).parent / "fixtures" / "engine_failure_server.py"
    async with subprocess_client(script=fixture) as client:
        run = await client.call_tool("run_simulation", {"cashiers": 1, "customers": 1})
        comparison = await client.call_tool(
            "compare_simulations",
            {"experiments": [{"cashiers": 1, "customers": 1}, {"cashiers": 3, "customers": 1}]},
        )
        for result in (run, comparison):
            assert result.is_error
            assert result.structured_content["error"]["code"] == "ENGINE_ERROR"
            assert "fixture-" not in json.dumps(result.model_dump(mode="json"))
        healthy = await client.call_tool("run_simulation", {"cashiers": 1, "customers": 2})
        assert not healthy.is_error


async def test_restarting_stdio_server_preserves_deterministic_results():
    async with subprocess_client() as first:
        first_result = await first.call_tool(
            "run_simulation", {"cashiers": 3, "customers": 5, "seed": 7}
        )
    async with subprocess_client() as second:
        second_result = await second.call_tool(
            "run_simulation", {"cashiers": 3, "customers": 5, "seed": 7}
        )
    assert not first_result.is_error and not second_result.is_error
    assert first_result.structured_content == second_result.structured_content
