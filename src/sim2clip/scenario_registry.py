"""Shared static scene definitions and exact tool contracts; no engine dispatch.

Lazy validation/report callbacks keep protocol discovery independent of Agent
initialization. Only the MCP server owns the simulation execution handlers.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from sim2clip.scenarios.server_contracts import (
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerSimulationConfig,
    ServerSimulationResult,
)
from sim2clip.simulation.schemas import (
    ComparisonRequest,
    ComparisonResult,
    SimulationConfig,
    SimulationResult,
)

ScenarioId = Literal["queue", "server_load_balancing"]
SimulationInput = (
    SimulationConfig | ComparisonRequest | ServerSimulationConfig | ServerComparisonRequest
)
SingleResult = SimulationResult | ServerSimulationResult
ComparisonOutput = ComparisonResult | ServerComparisonResult
SimulationOutput = SingleResult | ComparisonOutput
Payload = dict[str, Any]


@dataclass(frozen=True)
class ToolContract:
    name: str
    scenario_id: ScenarioId
    description: str
    input_model: type[SimulationInput]
    output_model: type[SimulationOutput]
    comparison: bool

    def parse_request(self, arguments: Payload) -> SimulationInput:
        return self.input_model.model_validate(arguments)

    def parse_result(self, payload: Payload) -> SimulationOutput:
        return self.output_model.model_validate(payload)


def _queue_validator(name: str, arguments: Payload, result: SimulationOutput) -> None:
    from sim2clip.agent.queue_provenance import validate_queue_provenance
    from sim2clip.agent.validation import validate_response

    if not isinstance(result, (SimulationResult, ComparisonResult)):
        raise ValueError("Queue tool returned a different scene's result")
    validate_response(name, arguments, result)
    validate_queue_provenance(name, result)


def _server_validator(name: str, arguments: Payload, result: SimulationOutput) -> None:
    from sim2clip.agent.server_validation import (
        validate_server_comparison,
        validate_server_simulation,
    )

    if any(
        run.workload_source != "generated"
        for run in result_runs(result)
        if isinstance(run, ServerSimulationResult)
    ):
        raise ValueError("MCP server requests require generated samples from the declared seed")
    if name == "run_server_simulation" and isinstance(result, ServerSimulationResult):
        validate_server_simulation(result, ServerSimulationConfig.model_validate(arguments))
    elif name == "compare_server_simulations" and isinstance(result, ServerComparisonResult):
        validate_server_comparison(result, ServerComparisonRequest.model_validate(arguments))
    else:
        raise ValueError("Server tool returned a different scene's result")


def _queue_report(results: Sequence[SimulationOutput], context: Payload) -> str:
    from sim2clip.agent.reporting import queue_report

    return queue_report(results, context)


def _server_report(results: Sequence[SimulationOutput], context: Payload) -> str:
    from sim2clip.agent.reporting import server_report

    return server_report(results, context)


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: ScenarioId
    description: str
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    defaults: str
    tools: tuple[ToolContract, ...]
    maximum_entities: int
    maximum_comparison_work_units: int
    validator: Callable[[str, Payload, SimulationOutput], None]
    reporter: Callable[[Sequence[SimulationOutput], Payload], str]


_QUEUE_TOOLS = (
    ToolContract(
        "run_simulation",
        "queue",
        "Run a finite convenience-store FIFO queue experiment using SimPy. "
        "All times are seconds. Returns real metrics, samples, records and replay events.",
        SimulationConfig,
        SimulationResult,
        False,
    ),
    ToolContract(
        "compare_simulations",
        "queue",
        "Compare distinct cashier counts with the same customer samples per seed. "
        "Returns complete paired runs, summaries, signed differences and ties. "
        "The first experiment is the baseline; all times are seconds.",
        ComparisonRequest,
        ComparisonResult,
        True,
    ),
)
_SERVER_TOOLS = (
    ToolContract(
        "run_server_simulation",
        "server_load_balancing",
        "Simulate finite server requests with arrival times and work, per-server FIFO queues, "
        "one slot per server, heterogeneous work/second rates and "
        "Round Robin or Least Outstanding. "
        "All times are seconds. No network delay, timeout, failure or retry model.",
        ServerSimulationConfig,
        ServerSimulationResult,
        False,
    ),
    ToolContract(
        "compare_server_simulations",
        "server_load_balancing",
        "Compare server pools OR Round Robin versus Least Outstanding on the same request samples "
        "per seed. Supply stable group_id values and one comparison_axis. Returns full runs, "
        "response/wait/nearest-rank P95 metrics, per-seed differences and descriptive summaries. "
        "First group is baseline. Mean of run P95 values is not a pooled P95 "
        "or production forecast.",
        ServerComparisonRequest,
        ServerComparisonResult,
        True,
    ),
)

SCENARIOS: Mapping[ScenarioId, ScenarioDefinition] = MappingProxyType(
    {
        "queue": ScenarioDefinition(
            "queue",
            "便利店有限顾客、共享 FIFO 排队；适用于收银台容量与等待时间实验。",
            (
                "finite_customers",
                "shared_fifo",
                "cashier_capacity",
                "exponential_arrivals_and_service",
            ),
            ("只比较收银台数量", "不支持故障、重试、超时或网络模型"),
            "100顾客；到达间隔均值10秒、服务均值25秒；比较默认1/3收银台、seed42–46。",
            _QUEUE_TOOLS,
            10000,
            100000,
            _queue_validator,
            _queue_report,
        ),
        "server_load_balancing": ScenarioDefinition(
            "server_load_balancing",
            "服务器有限请求分流、独立 FIFO；适用于资源池或两种分流策略实验。",
            (
                "finite_requests",
                "per_server_fifo",
                "heterogeneous_rates",
                "round_robin",
                "least_outstanding",
                "exponential_or_batch_arrivals",
                "exponential_or_constant_work",
            ),
            ("单节点单处理槽", "无网络延迟、故障、重试、超时、扩缩容", "LO不按速率或工作量加权"),
            "1000请求；到达间隔均值0.01秒、工作量均值0.025、速率1工作单位/秒；seed42–46。"
            "策略比较默认两台同速服务器；容量比较默认一台与三台。到达假设必须披露。",
            _SERVER_TOOLS,
            10000,
            100000,
            _server_validator,
            _server_report,
        ),
    }
)
TOOLS: Mapping[str, ToolContract] = MappingProxyType(
    {tool.name: tool for scenario in SCENARIOS.values() for tool in scenario.tools}
)


def tool_contract(name: str) -> ToolContract:
    try:
        return TOOLS[name]
    except KeyError as exc:
        raise ValueError(f"Tool is not registered: {name}") from exc


def result_runs(result: SimulationOutput) -> tuple[SingleResult, ...]:
    if isinstance(result, ComparisonResult):
        return result.results
    if isinstance(result, ServerComparisonResult):
        return tuple(run.result for run in result.results)
    return (result,)


def validate_tool_response(name: str, arguments: Payload, result: SimulationOutput) -> None:
    contract = tool_contract(name)
    if not isinstance(result, contract.output_model):
        raise ValueError("Wrong tool response type")
    SCENARIOS[contract.scenario_id].validator(name, arguments, result)
