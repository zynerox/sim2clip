"""Call four real stdio tools with fixed inputs; this is an MCP demo, not LLM planning."""

import argparse
import asyncio
import json
from pathlib import Path

from sim2clip.agent.mcp_client import SimulationMCPClient
from sim2clip.scenario_registry import SimulationInput, tool_contract, validate_tool_response
from sim2clip.scenarios.server_contracts import (
    ServerComparisonRequest,
    ServerConfig,
    ServerExperiment,
    ServerSimulationConfig,
)
from sim2clip.simulation.schemas import ComparisonRequest, SimulationConfig


async def run(output: Path) -> None:
    pool = (
        ServerConfig(server_id=1, processing_rate=1),
        ServerConfig(server_id=2, processing_rate=2),
    )
    server = ServerSimulationConfig(requests=30, servers=pool)
    requests: dict[str, SimulationInput] = {
        "run_simulation": SimulationConfig(customers=10, cashiers=1),
        "compare_simulations": ComparisonRequest(
            experiments=(
                SimulationConfig(customers=10, cashiers=1),
                SimulationConfig(customers=10, cashiers=3),
            ),
            seeds=(42, 43),
        ),
        "run_server_simulation": server,
        "compare_server_simulations": ServerComparisonRequest(
            comparison_axis="routing_strategy",
            seeds=(42, 43),
            experiments=(
                ServerExperiment(group_id="rr", config=server),
                ServerExperiment(
                    group_id="lo",
                    config=ServerSimulationConfig(
                        requests=30, servers=pool, routing_strategy="least_outstanding"
                    ),
                ),
            ),
        ),
    }
    output = output.resolve()
    paths = {name: output / f"{name}.json" for name in requests}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Demo result exists; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    async with SimulationMCPClient() as client:
        for name, request in requests.items():
            arguments = request.model_dump(mode="json")
            payload = await client.call(name, arguments)
            result = tool_contract(name).parse_result(payload)
            validate_tool_response(name, arguments, result)
            with paths[name].open("x", encoding="utf-8") as stream:
                stream.write(result.model_dump_json(indent=2) + "\n")
        print(
            json.dumps(
                {
                    "discovered": [t.name for t in client.tools],
                    "validated_results": {k: str(v) for k, v in paths.items()},
                    "planner": "fixed MCP demonstration; no LLM",
                },
                ensure_ascii=False,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/multi-scenario-mcp"))
    asyncio.run(run(parser.parse_args().output))


if __name__ == "__main__":
    main()
