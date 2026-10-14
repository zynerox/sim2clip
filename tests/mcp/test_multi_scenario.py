"""Real stdio, registered contracts, server-only engines and protocol failures."""

import copy
import json

import pytest
from mcp import Client
from mcp.server import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent

from sim2clip.agent.mcp_client import SimulationMCPClient, ToolFailure
from sim2clip.mcp_server import simulation_server as server
from sim2clip.scenario_registry import TOOLS, validate_tool_response
from sim2clip.scenarios.server_contracts import ServerComparisonRequest, ServerSimulationConfig
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation

pytestmark = pytest.mark.anyio


def server_request(seeds=(42, 43), count=10):
    return {
        "comparison_axis": "routing_strategy",
        "seeds": list(seeds),
        "experiments": [
            {
                "group_id": "rr",
                "config": {
                    "requests": count,
                    "seed": seeds[0],
                    "servers": [{"server_id": 1}, {"server_id": 2}],
                },
            },
            {
                "group_id": "lo",
                "config": {
                    "requests": count,
                    "seed": seeds[0],
                    "servers": [{"server_id": 1}, {"server_id": 2}],
                    "routing_strategy": "least_outstanding",
                },
            },
        ],
    }


async def test_four_tools_discovered_and_called_over_stdio_with_real_engines(tmp_path):
    async with SimulationMCPClient() as client:
        assert {tool.name for tool in client.tools} == set(TOOLS)
        requests = {
            "run_simulation": {"customers": 3, "cashiers": 1},
            "compare_simulations": {
                "experiments": [{"customers": 3, "cashiers": 1}, {"customers": 3, "cashiers": 3}],
                "seeds": [42],
            },
            "run_server_simulation": {
                "requests": 3,
                "servers": [{"server_id": 1, "processing_rate": 2}],
            },
            "compare_server_simulations": server_request(),
        }
        for name, arguments in requests.items():
            payload = await client.call(name, arguments)
            result = TOOLS[name].parse_result(payload)
            validate_tool_response(name, arguments, result)
            assert result.events if hasattr(result, "events") else result.results
        single = await client.call("run_server_simulation", requests["run_server_simulation"])
        assert single == run_server_simulation(
            ServerSimulationConfig.model_validate(requests["run_server_simulation"])
        ).model_dump(mode="json")
        compared = await client.call("compare_server_simulations", server_request())
        assert compared == run_server_comparison(
            ServerComparisonRequest.model_validate(server_request())
        ).model_dump(mode="json")
        with pytest.raises(ToolFailure, match="Unsupported"):
            await client.call("unknown", {})


@pytest.mark.parametrize(
    "kind", ["missing", "duplicate", "unknown", "input", "output", "description"]
)
async def test_client_rejects_invalid_tool_discovery(kind):
    definitions = copy.deepcopy(server._tool_definitions())
    if kind == "missing":
        definitions.pop()
    elif kind == "duplicate":
        definitions.append(definitions[0])
    elif kind == "unknown":
        definitions[0].name = "fabricate"
    elif kind == "input":
        definitions[2].input_schema["properties"]["requests"]["maximum"] = 9
    elif kind == "output":
        definitions[2].output_schema = None
    else:
        definitions[2].description = None

    async def listing(ctx, params):
        return ListToolsResult(tools=definitions)

    invalid = Server("bad-discovery", on_list_tools=listing, on_call_tool=server._call_tool)
    with pytest.raises(ValueError):
        async with SimulationMCPClient(invalid):
            pass


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("run_server_simulation", {"requests": True}),
        ("run_server_simulation", {"servers": [{"server_id": 1, "capacity": 2}]}),
        ("compare_server_simulations", {"comparison_axis": "routing_strategy", "experiments": []}),
    ],
)
async def test_server_invalid_parameters_never_reach_engine(monkeypatch, name, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached server engine")

    monkeypatch.setattr(server, "engine_run_server_simulation", forbidden)
    monkeypatch.setattr(server, "engine_run_server_comparison", forbidden)
    async with SimulationMCPClient(server.create_server()) as client:
        with pytest.raises(ToolFailure) as failure:
            await client.call(name, arguments)
    assert failure.value.code == "INVALID_PARAMETERS"


@pytest.mark.parametrize("invalid_output", [False, True])
async def test_server_engine_failure_and_invalid_response_are_protocol_errors(
    monkeypatch, invalid_output
):
    def fail(*args, **kwargs):
        if invalid_output:
            return {"metrics": {}}
        raise RuntimeError("private detail")

    monkeypatch.setattr(server, "engine_run_server_simulation", fail)
    monkeypatch.setattr(server, "engine_run_server_comparison", fail)
    async with Client(server.create_server()) as client:
        for name, args in (
            ("run_server_simulation", {"requests": 3}),
            ("compare_server_simulations", server_request()),
        ):
            result = await client.call_tool(name, args)
            assert result.is_error
            assert result.structured_content["error"]["code"] == (
                "INVALID_RESULT" if invalid_output else "ENGINE_ERROR"
            )
            assert "private detail" not in str(result.structured_content)


@pytest.mark.parametrize(
    "kind", ["text_error", "nonjson", "good_payload", "bad_error", "missing_flag"]
)
async def test_client_honors_is_error_and_structured_or_text_error_envelopes(kind):
    async def handler(ctx, params):
        payload = {"error": {"code": "TEMPORARY_TOOL_ERROR", "message": "retry", "retryable": True}}
        text = json.dumps(payload)
        if kind == "good_payload":
            response = await server._call_tool(ctx, params)
            response.is_error = True
            return response
        if kind == "nonjson":
            text = "plain failure"
        if kind == "bad_error":
            payload = {"error": "invalid envelope"}
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=None if kind in ("text_error", "nonjson") else payload,
            is_error=kind != "missing_flag",
        )

    fixture = Server("error-envelope", on_list_tools=server._list_tools, on_call_tool=handler)
    async with SimulationMCPClient(fixture) as client:
        with pytest.raises(ToolFailure) as error:
            await client.call("run_server_simulation", {"requests": 3})
    expected = {"text_error": "TEMPORARY_TOOL_ERROR", "missing_flag": "INVALID_RESULT"}.get(
        kind, "TOOL_ERROR"
    )
    assert error.value.code == expected
    assert error.value.retryable is (kind == "text_error")
