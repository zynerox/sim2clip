"""Test actual MCP messages in-process, including boundary failure mapping."""

import json
from collections.abc import Sequence

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from sim2clip.mcp_server import simulation_server
from sim2clip.scenarios.server_contracts import (
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerSimulationConfig,
    ServerSimulationResult,
)
from sim2clip.simulation import (
    ComparisonRequest,
    ComparisonResult,
    SimulationConfig,
    SimulationResult,
    run_comparison,
    run_simulation,
)

pytestmark = pytest.mark.anyio


def error_payload(result: CallToolResult) -> dict:
    assert result.is_error
    assert result.structured_content is not None
    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == result.structured_content
    json.dumps(result.structured_content, allow_nan=False)
    return result.structured_content["error"]


async def test_discovery_publishes_exact_shared_input_and_output_schemas():
    async with Client(simulation_server.create_server()) as client:
        listing = await client.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        assert set(tools) == {
            "run_simulation",
            "compare_simulations",
            "run_server_simulation",
            "compare_server_simulations",
        }
        contracts = {
            "run_simulation": (SimulationConfig, SimulationResult),
            "compare_simulations": (ComparisonRequest, ComparisonResult),
            "run_server_simulation": (ServerSimulationConfig, ServerSimulationResult),
            "compare_server_simulations": (ServerComparisonRequest, ServerComparisonResult),
        }
        for name, (input_model, output_model) in contracts.items():
            tool = tools[name]
            assert tool.input_schema == input_model.model_json_schema()
            assert tool.output_schema == output_model.model_json_schema(mode="serialization")
            assert tool.input_schema["additionalProperties"] is False
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.idempotent_hint is True


async def test_run_tool_matches_engine_and_has_serializable_output():
    arguments = {"cashiers": 3, "customers": 10, "seed": 42}
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool("run_simulation", arguments)
    assert not result.is_error
    actual = SimulationResult.model_validate(result.structured_content)
    assert actual == run_simulation(SimulationConfig.model_validate(arguments))
    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == result.structured_content
    assert json.loads(json.dumps(result.structured_content, allow_nan=False))["events"]


async def test_compare_tool_matches_engine_and_preserves_shared_samples():
    request = ComparisonRequest(
        experiments=(
            SimulationConfig(cashiers=1, customers=10),
            SimulationConfig(cashiers=3, customers=10),
        ),
        seeds=(42, 43),
    )
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool("compare_simulations", request.model_dump(mode="json"))
    assert not result.is_error
    actual = ComparisonResult.model_validate(result.structured_content)
    assert actual == run_comparison(request.experiments, seeds=request.seeds)
    for seed in actual.seeds:
        runs = [run for run in actual.results if run.config.seed == seed]
        assert runs[0].customer_samples == runs[1].customer_samples
    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == result.structured_content


async def test_defaults_come_from_shared_models():
    async with Client(simulation_server.create_server()) as client:
        run = await client.call_tool("run_simulation", {"cashiers": 3})
        comparison = await client.call_tool(
            "compare_simulations",
            {"experiments": [{"cashiers": 1, "customers": 2}, {"cashiers": 3, "customers": 2}]},
        )
    assert SimulationResult.model_validate(run.structured_content).config == SimulationConfig(
        cashiers=3
    )
    assert ComparisonResult.model_validate(comparison.structured_content).seeds == (
        42,
        43,
        44,
        45,
        46,
    )


@pytest.mark.parametrize(
    ("tool", "arguments", "location"),
    [
        ("run_simulation", {"cashiers": 1, "customers": -100}, ["customers"]),
        ("run_simulation", {"cashiers": True}, ["cashiers"]),
        ("run_simulation", {"cashiers": "3"}, ["cashiers"]),
        ("run_simulation", {}, ["cashiers"]),
        ("run_simulation", {"cashiers": 1, "typo": 8}, ["typo"]),
        ("run_simulation", {"config": {"cashiers": 1}}, ["config"]),
        ("compare_simulations", {"experiments": [{"cashiers": 1}]}, ["experiments"]),
        (
            "compare_simulations",
            {"experiments": [{"cashiers": 1}, {"cashiers": 3}], "unknown": 1},
            ["unknown"],
        ),
        (
            "compare_simulations",
            {"experiments": [{"cashiers": 1, "typo": 8}, {"cashiers": 3}]},
            ["experiments", 0, "typo"],
        ),
        (
            "compare_simulations",
            {"experiments": [{"cashiers": 1}, {"cashiers": 3}], "seeds": [-1]},
            ["seeds", 0],
        ),
    ],
)
async def test_invalid_parameters_rejected_before_engine_execution(
    monkeypatch, tool, arguments, location
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input must not reach the engine")

    monkeypatch.setattr(simulation_server, "engine_run_simulation", forbidden)
    monkeypatch.setattr(simulation_server, "engine_run_comparison", forbidden)
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool(tool, arguments)
    error = error_payload(result)
    assert error["code"] == "INVALID_PARAMETERS"
    assert error["retryable"] is False
    assert location in [issue["location"] for issue in error["issues"]]


async def test_comparison_business_validation_is_reused():
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool(
            "compare_simulations",
            {"experiments": [{"cashiers": 1}, {"cashiers": 3, "mean_service_time": 99.0}]},
        )
    error = error_payload(result)
    assert error["code"] == "INVALID_PARAMETERS"
    assert "same workload" in error["issues"][0]["message"]


@pytest.mark.parametrize("tool", ["run_simulation", "compare_simulations"])
async def test_engine_exception_is_mapped_and_same_session_can_recover(monkeypatch, tool):
    def fail_run(config: SimulationConfig) -> SimulationResult:
        raise RuntimeError("private internal engine detail")

    def fail_compare(
        experiments: Sequence[SimulationConfig], *, seeds: Sequence[int] | None = None
    ) -> ComparisonResult:
        raise ValueError("private internal engine detail")

    async with Client(simulation_server.create_server()) as client:
        with monkeypatch.context() as patch:
            patch.setattr(simulation_server, "engine_run_simulation", fail_run)
            patch.setattr(simulation_server, "engine_run_comparison", fail_compare)
            arguments = (
                {"cashiers": 3, "customers": 2}
                if tool == "run_simulation"
                else {
                    "experiments": [
                        {"cashiers": 1, "customers": 2},
                        {"cashiers": 3, "customers": 2},
                    ]
                }
            )
            failure = await client.call_tool(tool, arguments)
        error = error_payload(failure)
        assert error["code"] == "ENGINE_ERROR"
        assert error["retryable"] is False
        assert "private internal engine detail" not in json.dumps(failure.model_dump(mode="json"))
        success = await client.call_tool("run_simulation", {"cashiers": 1, "customers": 2})
        assert not success.is_error


@pytest.mark.parametrize("tool", ["run_simulation", "compare_simulations"])
async def test_invalid_engine_response_is_not_returned_as_success(monkeypatch, tool):
    monkeypatch.setattr(simulation_server, "engine_run_simulation", lambda config: {"metrics": {}})
    monkeypatch.setattr(
        simulation_server, "engine_run_comparison", lambda *args, **kwargs: {"results": []}
    )
    arguments = (
        {"cashiers": 1, "customers": 2}
        if tool == "run_simulation"
        else {"experiments": [{"cashiers": 1}, {"cashiers": 3}], "seeds": [42]}
    )
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool(tool, arguments)
    assert error_payload(result)["code"] == "INVALID_RESULT"


async def test_unknown_tool_has_an_explicit_error():
    async with Client(simulation_server.create_server()) as client:
        result = await client.call_tool("not_a_tool", {})
    assert error_payload(result)["code"] == "UNKNOWN_TOOL"
