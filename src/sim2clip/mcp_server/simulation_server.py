"""Real stdio MCP server, using exactly the simulation layer's contracts.

The SDK low-level server lets tools advertise the existing models as flat
input schemas, without generating a second argument model or config wrapper.
Only validation, dispatch, serialization and protocol error mapping live here.
"""

import json
import logging
import sys
from collections.abc import Callable
from functools import partial
from typing import Literal

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, ValidationError

from sim2clip import __version__
from sim2clip.scenario_registry import TOOLS, SimulationOutput
from sim2clip.scenarios.server_contracts import ServerComparisonRequest, ServerSimulationConfig
from sim2clip.simulation import (
    ComparisonRequest,
    SimulationConfig,
)
from sim2clip.simulation import run_comparison as engine_run_comparison
from sim2clip.simulation import run_simulation as engine_run_simulation
from sim2clip.simulation.server_comparison import (
    run_server_comparison as engine_run_server_comparison,
)
from sim2clip.simulation.server_model import run_server_simulation as engine_run_server_simulation

logger = logging.getLogger(__name__)
ErrorCode = Literal["INVALID_PARAMETERS", "ENGINE_ERROR", "INVALID_RESULT", "UNKNOWN_TOOL"]


def _tool_definitions() -> list[Tool]:
    annotations = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    return [
        Tool(
            name=contract.name,
            description=contract.description,
            input_schema=contract.input_model.model_json_schema(),
            output_schema=contract.output_model.model_json_schema(mode="serialization"),
            annotations=annotations,
        )
        for contract in TOOLS.values()
    ]


def _error(
    code: ErrorCode, message: str, *, issues: list[dict[str, object]] | None = None
) -> CallToolResult:
    # This is a protocol error envelope, not a second simulation schema.
    payload = {
        "error": {"code": code, "message": message, "retryable": False, "issues": issues or []}
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, allow_nan=False))],
        structured_content=payload,
        is_error=True,
    )


def _success(result: BaseModel) -> CallToolResult:
    payload = result.model_dump(mode="json")
    return CallToolResult(
        content=[
            TextContent(
                type="text", text=json.dumps(payload, allow_nan=False, separators=(",", ":"))
            )
        ],
        structured_content=payload,
        is_error=False,
    )


async def _list_tools(
    ctx: ServerRequestContext[dict[str, object]], params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=_tool_definitions())


async def _call_tool(
    ctx: ServerRequestContext[dict[str, object]], params: CallToolRequestParams
) -> CallToolResult:
    arguments = params.arguments if params.arguments is not None else {}
    operation: Callable[[], SimulationOutput]
    try:
        if params.name == "run_simulation":
            config = SimulationConfig.model_validate(arguments)
            operation = partial(engine_run_simulation, config)
        elif params.name == "compare_simulations":
            request = ComparisonRequest.model_validate(arguments)
            operation = partial(engine_run_comparison, request.experiments, seeds=request.seeds)
        elif params.name == "run_server_simulation":
            server_config = ServerSimulationConfig.model_validate(arguments)
            operation = partial(engine_run_server_simulation, server_config)
        elif params.name == "compare_server_simulations":
            server_request = ServerComparisonRequest.model_validate(arguments)
            operation = partial(engine_run_server_comparison, server_request)
        else:
            return _error("UNKNOWN_TOOL", f"Unknown tool: {params.name}")
    except ValidationError as exc:
        issues: list[dict[str, object]] = [
            {"location": list(issue["loc"]), "type": issue["type"], "message": issue["msg"]}
            for issue in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        return _error(
            "INVALID_PARAMETERS", "Arguments do not satisfy the shared input schema.", issues=issues
        )

    try:
        # Each engine call has local state. Keep synchronous computation off the
        # protocol event loop without introducing scheduling logic into the engine.
        result = await anyio.to_thread.run_sync(operation)
    except Exception:
        logger.exception("Tool %s failed during engine execution", params.name)
        return _error("ENGINE_ERROR", "Simulation execution failed; inspect the server stderr log.")

    try:
        validated = TOOLS[params.name].output_model.model_validate(result)
        return _success(validated)
    except Exception:
        logger.exception("Tool %s returned an invalid or unserializable result", params.name)
        return _error(
            "INVALID_RESULT", "Engine output does not satisfy the shared response schema."
        )


def create_server() -> Server[dict[str, object]]:
    """Create a fresh server; no process, file or network side effects at import."""
    return Server[dict[str, object]](
        "sim2clip-simulation",
        version=__version__,
        instructions=(
            "Use run_simulation for one queue experiment and "
            "compare_simulations for a paired comparison. "
            "Use run_server_simulation or compare_server_simulations for server load balancing. "
            "All times are seconds. These tools compute results; do not invent simulation metrics."
        ),
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def serve_stdio() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    logger.info("Starting Sim2Clip simulation MCP server over stdio")
    try:
        anyio.run(serve_stdio)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
