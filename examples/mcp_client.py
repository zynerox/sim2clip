"""Discover and call the real stdio MCP server; save schema-validated demo results."""

import argparse
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult

from sim2clip.simulation import (
    ComparisonRequest,
    ComparisonResult,
    SimulationConfig,
    SimulationResult,
)


def require_success(result: CallToolResult) -> None:
    if result.is_error:
        raise RuntimeError(f"MCP tool failed: {result.structured_content}")
    if result.structured_content is None:
        raise RuntimeError("MCP tool returned no structured content")


async def run_demo(output: Path) -> None:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "sim2clip.mcp_server"])
    async with Client(parameters, read_timeout_seconds=30.0) as client:
        tools = await client.list_tools()
        print("Discovered MCP tools:", ", ".join(tool.name for tool in tools.tools))

        config = SimulationConfig(cashiers=3, customers=100, seed=42)
        run_response = await client.call_tool("run_simulation", config.model_dump(mode="json"))
        require_success(run_response)
        run = SimulationResult.model_validate(run_response.structured_content)

        request = ComparisonRequest(
            experiments=(SimulationConfig(cashiers=1), SimulationConfig(cashiers=3)),
            seeds=(42, 43, 44, 45, 46),
        )
        comparison_response = await client.call_tool(
            "compare_simulations", request.model_dump(mode="json")
        )
        require_success(comparison_response)
        comparison = ComparisonResult.model_validate(comparison_response.structured_content)

    output.mkdir(parents=True, exist_ok=True)
    (output / "run.json").write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (output / "comparison.json").write_text(
        comparison.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    for summary in comparison.summaries:
        print(f"cashiers={summary.cashiers}: mean wait={summary.average_wait_time.mean:.2f}s")
    print(f"Validated MCP results: {output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/mcp-demo"))
    arguments = parser.parse_args()
    anyio.run(run_demo, arguments.output)


if __name__ == "__main__":
    main()
