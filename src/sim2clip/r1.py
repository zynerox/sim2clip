"""Bounded, private R1 acceptance support; never selects a tool for the model.

The ordinary planner and graph execute the unchanged schemas and engines. The HTTP guard
counts before dispatch and persists only a field whitelist, including failures.
"""

import asyncio
import json
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI

from sim2clip.agent.artifacts import ArtifactStore, json_bytes
from sim2clip.agent.planner import ModelPlanner, PlannerFailure, PlanningEnvelope
from sim2clip.agent.public_errors import public_errors, task_output
from sim2clip.agent.runtime import open_workflow
from sim2clip.config import ConfigurationError, ModelSettings
from sim2clip.scenario_registry import TOOLS, tool_contract, validate_tool_response
from sim2clip.scenarios.server_contracts import ServerSimulationResult
from sim2clip.simulation.schemas import SimulationResult
from sim2clip.visualization.html import render_html
from sim2clip.visualization.public import public_context, public_report
from sim2clip.visualization.timeline import execution_timeline

MODEL = "qwen3.5-plus"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ENDPOINT = BASE_URL + "/chat/completions"


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    tool: str | None
    axis: str | None = None


CASES = (
    Case(
        "A", "100 位顾客排队，比较 1 个收银台和 3 个收银台的平均等待时间。", "compare_simulations"
    ),
    Case(
        "B",
        "1000 个请求进入系统，比较 1 台和 3 台服务器的响应时间。",
        "compare_server_simulations",
        "server_pool",
    ),
    Case(
        "C",
        "同样的两台服务器，Round Robin 和 Least Outstanding 哪个平均响应时间更低？",
        "compare_server_simulations",
        "routing_strategy",
    ),
    Case(
        "D1",
        "服务器老是排队，多加两台机器能改善多少？",
        "compare_server_simulations",
        "server_pool",
    ),
    Case("D2", "收银口只开一个太堵了，三个口能让这 100 位顾客少等多久？", "compare_simulations"),
    Case("E", "帮我模拟明天的天气变化。", None),
    Case(
        "F1",
        "比较 1 台和 3 台服务器，其他参数用默认值。",
        "compare_server_simulations",
        "server_pool",
    ),
    Case("F2", "模拟 -5 个请求，比较 1 台和 3 台服务器。", None),
    Case("G", "用 2 个收银台模拟 20 位顾客排队。", "run_simulation"),
    Case("H", "用两台服务器和 Round Robin 策略模拟 20 个请求。", "run_server_simulation"),
)
SMOKE_IDS = ("A", "B", "E")
REVERIFY_CASES = (
    Case("G", "用2个收银台模拟20位顾客排队。", "run_simulation"),
    Case("H", "用两台服务器和Round Robin策略模拟20个请求。", "run_server_simulation"),
)


def single_report_checks(case: Case, result: Any, report: str) -> dict[str, bool]:
    """Acceptance assertions only; never construct or alter the model's plan."""
    if case.id not in {"G", "H"}:
        return {}
    single = isinstance(result, (SimulationResult, ServerSimulationResult))
    parameters_match = (
        isinstance(result, SimulationResult)
        and result.config.customers == 20
        and result.config.cashiers == 2
        if case.id == "G"
        else isinstance(result, ServerSimulationResult)
        and result.config.requests == 20
        and len(result.config.servers) == 2
        and result.config.routing_strategy == "round_robin"
    )
    metric_present = (
        isinstance(result, SimulationResult)
        and f"| 2 | 20 | {result.metrics.average_wait_time:.2f} |" in report
        if case.id == "G"
        else isinstance(result, ServerSimulationResult)
        and (
            f"| {result.metrics.average_response_time:.6f} | "
            f"{result.metrics.p95_response_time:.6f} |"
        )
        in report
    )
    return {
        "single_result": single,
        "parameters_match_requested_single": parameters_match,
        "public_goal_is_single": "实验目标：描述单次" in report,
        "no_invented_comparison": not any(
            text in report
            for text in (
                "实验目标：比较",
                "comparison_id：",
                "baseline：",
                "最低的组：",
                "最短的配置为",
            )
        ),
        "metrics_match_verified_result": metric_present,
        "assumptions_disclosed": (
            "模型假设：" in report
            if case.id == "G"
            else "到达假设" in report and "工作量假设" in report
        ),
    }


@dataclass(frozen=True)
class Limits:
    total_requests: int = 6
    case_requests: int = 2
    output_tokens: int = 4096
    model_seconds: float = 60.0
    case_seconds: float = 240.0
    request_bytes: int = 64000

    def __post_init__(self) -> None:
        if not (1 <= self.total_requests <= 14 and 1 <= self.case_requests <= 2):
            raise ValueError("Invalid R1 request limits")
        if not (1 <= self.output_tokens <= 4096 and 1 <= self.request_bytes <= 64000):
            raise ValueError("Invalid R1 token/body limits")
        if not (0 < self.model_seconds <= 60 and 0 < self.case_seconds <= 240):
            raise ValueError("Invalid R1 deadlines")


def configuration_review(env_file: Path | None = None) -> dict[str, Any]:
    """Load through the production loader; do not serialize the SecretStr."""
    path = env_file or Path.cwd() / ".env"
    file_values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    names = ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")
    sources = {
        name: {
            "process_environment_present": name in os.environ,
            "dotenv_present": name in file_values,
            "effective_source": "process_environment"
            if name in os.environ
            else ".env"
            if name in file_values
            else "missing",
        }
        for name in names
    }
    review: dict[str, Any] = {"dotenv_exists": path.is_file(), "variables": sources}
    try:
        settings = ModelSettings.load(env_file)
    except ConfigurationError:
        return {**review, "configuration_valid": False, "error": "MODEL_CONFIGURATION_ERROR"}
    return {
        **review,
        "configuration_valid": True,
        "api_key_nonempty": True,
        "model_matches": settings.model_name == MODEL,
        "endpoint_matches": settings.base_url == BASE_URL,
        "model": settings.model_name if settings.model_name == MODEL else "unexpected_model",
        "endpoint": settings.base_url if settings.base_url == BASE_URL else "unexpected_endpoint",
        "credential_validity": "not_tested",
        "remaining_free_tokens": None,
    }


def cost_cny(input_tokens: int, output_tokens: int) -> float:
    """Gross published rate, no free allowance/cache/Batch discount assumed.

    Use conservative decimal K thresholds; supplier billing is authoritative.
    Output tokens already include reasoning tokens: do not add them twice.
    """
    if min(input_tokens, output_tokens) < 0:
        raise ValueError("Token counts must be nonnegative")
    rates = (
        (0.8, 4.8)
        if input_tokens <= 128000
        else (2.0, 12.0)
        if input_tokens <= 256000
        else (4.0, 24.0)
    )
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


# No arbitrary strings (including group IDs, reasons, messages or call IDs) are
# copied. An enum must match a registered constant; group IDs become positions.
_STRINGS = {
    "1",
    "queue",
    "convenience_store_rush",
    "exponential",
    "second",
    "fifo",
    "server_load_balancing",
    "server_queue_network",
    "round_robin",
    "least_outstanding",
    "batch",
    "constant",
    "normalized_work",
    "complete_then_dispatch",
    "routing_strategy",
    "server_pool",
}
_FIELDS = {
    "request",
    "experiments",
    "seeds",
    "config",
    "schema_version",
    "simulation_type",
    "scenario",
    "cashiers",
    "customers",
    "seed",
    "arrival_distribution",
    "mean_interarrival_time",
    "service_distribution",
    "mean_service_time",
    "time_unit",
    "queue_discipline",
    "requests",
    "servers",
    "server_id",
    "processing_rate",
    "capacity",
    "routing_strategy",
    "arrivals",
    "workload",
    "mode",
    "arrival_time",
    "mean_work",
    "work",
    "work_unit",
    "scheduling_policy",
    "comparison_axis",
    "group_id",
}


def safe_arguments(value: Any, *, field: str = "", depth: int = 0) -> Any:
    if depth > 10:
        return "omitted"
    if isinstance(value, dict):
        return {
            k: safe_arguments(v, field=k, depth=depth + 1) for k, v in value.items() if k in _FIELDS
        }
    if isinstance(value, (tuple, list)):
        return [safe_arguments(item, field=field, depth=depth + 1) for item in value[:32]]
    if field == "group_id":
        return "group_id_redacted"
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else "nonfinite_number"
    return value if isinstance(value, str) and value in _STRINGS else "redacted_value"


def projected_call(name: Any, args: Any, index: int) -> dict[str, Any]:
    registered = isinstance(name, str) and name in TOOLS
    valid = False
    try:
        envelope = PlanningEnvelope.model_validate(args)
        if registered:
            tool_contract(name).parse_request(envelope.request)
            valid = True
    except (ValueError, TypeError):
        pass
    return {
        "index": index,
        "name": name if registered else "unregistered_tool",
        "args_projection": safe_arguments(args),
        "envelope_and_request_schema_valid": valid,
        "planning_metadata_present": isinstance(args, dict)
        and all(k in args for k in ("experiment_goal", "rationale", "assumptions")),
        "assumption_count": len(args["assumptions"])
        if isinstance(args, dict) and isinstance(args.get("assumptions"), list)
        else None,
    }


def usage_projection(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields):
        return None
    result = {k: usage[k] for k in fields}
    details = usage.get("completion_tokens_details")
    if (
        isinstance(details, dict)
        and type(details.get("reasoning_tokens")) is int
        and details["reasoning_tokens"] >= 0
    ):
        result["reasoning_tokens_included"] = details["reasoning_tokens"]
    return result


class Audit:
    """One sequential worker owns the atomic journal; reload across workers."""

    def __init__(self, root: Path, limits: Limits, *, remote: bool) -> None:
        self.root, self.limits = root.resolve(), limits
        self.store = ArtifactStore(self.root)
        self.path = self.root / "audit.json"
        self.data: dict[str, Any] = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {
                "evidence_type": "real_remote" if remote else "local_http_fixture",
                "model": MODEL,
                "endpoint_type": "bailian_beijing_openai_compatible_chat_completions",
                "request_limit": limits.total_requests,
                "requests": [],
                "stop_reason": None,
            }
        )
        if self.data["request_limit"] != limits.total_requests or self.data["evidence_type"] != (
            "real_remote" if remote else "local_http_fixture"
        ):
            raise ValueError("R1 journal identity mismatch")

    def save(self) -> None:
        self.store.write(self.path, json_bytes(self.data))

    def stop(self, code: str) -> None:
        self.data["stop_reason"] = code
        self.save()


class RequestBlocked(RuntimeError):
    pass


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, audit: Audit, case_id: str) -> None:
        self.inner, self.audit, self.case_id = inner, audit, case_id

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        rows = self.audit.data["requests"]
        limits = self.audit.limits
        if self.audit.data["stop_reason"]:
            raise RequestBlocked("R1 stopped before dispatch")
        if len(rows) >= limits.total_requests:
            self.audit.stop("TOTAL_REQUEST_LIMIT")
            raise RequestBlocked("R1 total request limit")
        if sum(row["case_id"] == self.case_id for row in rows) >= limits.case_requests:
            raise RequestBlocked("R1 case request limit")
        body = await request.aread()
        try:
            payload = json.loads(body)
            valid = (
                str(request.url) == ENDPOINT
                and request.method == "POST"
                and len(body) <= limits.request_bytes
                and payload["model"] == MODEL
                and payload["max_completion_tokens"] == limits.output_tokens
                and payload["enable_thinking"] is False
                and payload.get("stream", False) is False
                and payload.get("n", 1) == 1
                and "max_tokens" not in payload
                and payload.get("tool_choice", "auto") == "auto"
                and {t["function"]["name"] for t in payload["tools"]} == set(TOOLS)
            )
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            self.audit.stop("REQUEST_GUARD_REJECTED")
            raise RequestBlocked("R1 request does not satisfy the approved configuration")
        row: dict[str, Any] = {
            "sequence": len(rows) + 1,
            "case_id": self.case_id,
            "status": "in_flight",
            "max_completion_tokens": limits.output_tokens,
            "enable_thinking": False,
            "request_bytes": len(body),
            "http_status": None,
            "usage": None,
            "gross_cost_cny": None,
            "http_tool_calls": [],
            "AIMessage.tool_calls": None,
        }
        rows.append(row)
        # Count failed/timeout attempts too, and preserve dispatched counts if a
        # hard watchdog kills the worker. No request/header/body is serialized.
        self.audit.save()
        start = time.perf_counter()
        try:
            response = await self.inner.handle_async_request(request)
            row["http_status"] = response.status_code
            await response.aread()
            if response.status_code != 200:
                row["status"] = "provider_error"
                row["error_type"] = "ProviderHTTPError"
                self.audit.data["stop_reason"] = f"PROVIDER_HTTP_{response.status_code}"
            else:
                result = response.json()
                message = result["choices"][0]["message"]
                row["status"] = "http_success"
                row["usage"] = usage_projection(result.get("usage"))
                if row["usage"] is not None:
                    row["gross_cost_cny"] = cost_cny(
                        row["usage"]["prompt_tokens"], row["usage"]["completion_tokens"]
                    )
                row["finish_reason"] = (
                    result["choices"][0].get("finish_reason")
                    if result["choices"][0].get("finish_reason")
                    in {"stop", "length", "tool_calls", "content_filter"}
                    else "other"
                )
                row["reported_model_matches"] = result.get("model") in {
                    MODEL,
                    "qwen3.5-plus-2026-02-15",
                }
                for index, call in enumerate(message.get("tool_calls") or []):
                    raw = call.get("function", {}).get("arguments")
                    try:
                        args = json.loads(raw)
                        json_valid = True
                    except (ValueError, TypeError):
                        args, json_valid = None, False
                    row["http_tool_calls"].append(
                        {
                            "type": "function" if call.get("type") == "function" else "other",
                            "function": projected_call(
                                call.get("function", {}).get("name"), args, index
                            ),
                            "arguments_json_valid": json_valid,
                        }
                    )
            return response
        except asyncio.CancelledError:
            row["status"] = "cancelled_or_timeout"
            self.audit.data["stop_reason"] = "MODEL_CANCELLED_OR_TIMEOUT"
            raise
        except Exception as exc:
            row["status"] = "request_error"
            row["error_type"] = (
                "TimeoutException"
                if isinstance(exc, httpx.TimeoutException)
                else "TransportError"
                if isinstance(exc, httpx.TransportError)
                else "InvalidProviderResponse"
            )
            self.audit.data["stop_reason"] = row["error_type"]
            raise
        finally:
            row["seconds"] = time.perf_counter() - start
            self.audit.save()

    async def aclose(self) -> None:
        await self.inner.aclose()


class NativeAudit(AsyncCallbackHandler):
    def __init__(self, audit: Audit) -> None:
        self.audit = audit

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        rows = self.audit.data["requests"]
        if not rows:
            return
        message = getattr(response.generations[0][0], "message", None)
        if isinstance(message, AIMessage):
            rows[-1]["AIMessage.tool_calls"] = [
                projected_call(c["name"], c["args"], i) for i, c in enumerate(message.tool_calls)
            ]
            rows[-1]["invalid_tool_call_count"] = len(message.invalid_tool_calls)
            rows[-1]["response_text_present"] = bool(message.content)
            # Content and extra fields (including hidden reasoning) are omitted.
            self.audit.save()


class BoundedPlanner:
    mode = "llm"

    def __init__(self, planner: ModelPlanner, audit: Audit, case_id: str) -> None:
        self.planner, self.audit, self.case_id = planner, audit, case_id

    async def plan(self, state: Any, tools: Any) -> AIMessage:
        rows = self.audit.data["requests"]
        if (
            self.audit.data["stop_reason"]
            or len(rows) >= self.audit.limits.total_requests
            or sum(r["case_id"] == self.case_id for r in rows) >= self.audit.limits.case_requests
        ):
            raise PlannerFailure(
                "R1_REQUEST_LIMIT_OR_STOP", "R1 stopped before another model request"
            )
        return await self.planner.plan(state, tools)


def build_model(settings: ModelSettings, audit: Audit, client: httpx.AsyncClient) -> ModelPlanner:
    if settings.model_name != MODEL or settings.base_url != BASE_URL:
        raise ConfigurationError("R1 requires the approved Bailian Beijing endpoint and model")
    model = ChatOpenAI(
        model=MODEL,
        api_key=settings.api_key,
        base_url=BASE_URL,
        timeout=audit.limits.model_seconds,
        max_retries=0,
        use_responses_api=False,
        stream_usage=False,
        streaming=False,
        max_completion_tokens=audit.limits.output_tokens,
        extra_body={"enable_thinking": False},
        http_async_client=client,
        callbacks=[NativeAudit(audit)],
        cache=False,
    )
    return ModelPlanner(model, timeout=audit.limits.model_seconds)


@contextmanager
def measured(timings: dict[str, Any], key: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[key] = time.perf_counter() - start


def checkpoint_spans(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Checkpoint intervals include I/O/dispatch, not isolated function times."""
    previous: datetime | None = None
    rows = []
    for event in timeline:
        current = datetime.fromisoformat(event["timestamp"]) if event.get("timestamp") else None
        rows.append(
            {
                "node": event["node"],
                "round": event["round"],
                "seconds": (current - previous).total_seconds()
                if current is not None and previous is not None
                else None,
            }
        )
        previous = current
    return rows


async def run_case(
    case: Case,
    settings: ModelSettings,
    audit: Audit,
    *,
    inner: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Production ModelPlanner + default real stdio MCP. No demo or keyword plan."""
    directory = audit.root / case.id
    directory.mkdir(exist_ok=False)
    start = time.perf_counter()
    transport = GuardedTransport(inner or httpx.AsyncHTTPTransport(retries=0), audit, case.id)
    summary: dict[str, Any] = {"case_id": case.id, "status": "failed", "acceptance_passed": False}
    timings: dict[str, Any] = {
        "mcp_direct_call_seconds": None,
        "simulation_compute_seconds": None,
        "unmeasured_reason": "MCP wire/engine internals have no separate timer",
    }
    summary["timings"] = timings
    phase = "initialization"
    try:
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            planner = BoundedPlanner(build_model(settings, audit, client), audit, case.id)
            async with open_workflow(
                planner,
                checkpoint_path=directory / "checkpoints.sqlite",
                artifact_root=directory / "runs",
            ) as workflow:
                async with asyncio.timeout(audit.limits.case_seconds):
                    phase = "workflow"
                    with measured(timings, "workflow_seconds"):
                        state = await workflow.run(case.query, thread_id=case.id)
                    history = [
                        {
                            "timestamp": h.created_at,
                            "step": (h.metadata or {}).get("step"),
                            "state": {
                                "decision_history": h.values.get("decision_history", []),
                                "tool_calls": h.values.get("tool_calls", []),
                            },
                        }
                        async for h in workflow.graph.aget_state_history(workflow.config(case.id))
                    ]
                    timeline = execution_timeline(list(reversed(history)), dict(state))
                    timings["checkpoint_node_spans"] = checkpoint_spans(timeline)
                    calls = state["tool_calls"]
                    native = [r for r in audit.data["requests"] if r["case_id"] == case.id]
                    first = (native[0].get("AIMessage.tool_calls") or []) if native else []
                    expected_scene = (
                        None if case.tool is None else tool_contract(case.tool).scenario_id
                    )
                    chosen = [c["name"] for c in first]
                    expected_tools = [] if case.tool is None else [case.tool]
                    summary.update(
                        status=state["status"],
                        scenario_id=state["scenario_id"],
                        correct_initial_tool=chosen == expected_tools
                        if native and native[0].get("AIMessage.tool_calls") is not None
                        else None,
                        correct_scenario=state["scenario_id"] == expected_scene,
                        first_parameters_accepted=any(
                            d["node"] == "parameter_validator"
                            and d["round"] == 1
                            and d["action"] == "execute"
                            for d in state["decision_history"]
                        ),
                        planning_rounds=state["planning_round"],
                        retry_counts=state["retry_counts"],
                        validation={
                            k: state["validation_result"].get(k)
                            for k in ("valid", "sufficient", "checked_calls")
                        },
                        nodes=[
                            {
                                k: row.get(k)
                                for k in ("node", "action", "round", "timestamp", "checkpoint_step")
                            }
                            for row in timeline
                        ],
                        error_codes=[
                            e["code"]
                            for e in state["errors"]
                            if e.get("code")
                            in {
                                "UNSUPPORTED_REQUEST",
                                "PLANNER_TIMEOUT",
                                "PLANNER_REQUEST_ERROR",
                                "INVALID_PLAN_JSON",
                                "INVALID_PLAN",
                                "R1_REQUEST_LIMIT_OR_STOP",
                                "INVALID_REQUEST_COUNT",
                                "INVALID_CUSTOMER_COUNT",
                            }
                        ],
                        failure_stage=state["errors"][-1].get("node") if state["errors"] else None,
                        feedback_code=state["feedback"].get("code")
                        if state["feedback"].get("code")
                        in {
                            "UNSUPPORTED_REQUEST",
                            "INVALID_PARAMETERS",
                            "INSUFFICIENT_EVIDENCE",
                            "INVALID_EVIDENCE",
                            "R1_REQUEST_LIMIT_OR_STOP",
                            "INVALID_REQUEST_COUNT",
                            "INVALID_CUSTOMER_COUNT",
                        }
                        else None,
                        mcp_calls=[
                            {
                                "name": c["name"],
                                "status": c["status"],
                                "arguments_projection": safe_arguments(c["arguments"]),
                                "cached": c.get("cached"),
                                "response_sha256": c.get("response", {}).get("sha256"),
                                "receipt_file": "runs/"
                                + case.id
                                + "/"
                                + Path(c["response"]["path"]).name.replace(".json", ".receipt.json")
                                if c.get("response")
                                else None,
                                "receipt_verified": c["status"] == "success"
                                and state["validation_result"].get("valid") is True,
                            }
                            for c in calls
                        ],
                    )
                    axis_ok = (
                        case.axis is None
                        or (state["design"] or {}).get("comparison_axis") == case.axis
                    )
                    published_report = ""
                    if state["status"] == "completed":
                        phase = "evidence_validation"
                        if workflow.verify_completed:
                            with measured(timings, "verify_completed_seconds"):
                                workflow.verify_completed(state)
                        store = ArtifactStore(directory / "runs")
                        last_call = [c for c in calls if c["status"] == "success"][-1]
                        with measured(timings, "receipt_load_parse_seconds"):
                            result = tool_contract(last_call["name"]).parse_result(
                                store.load(last_call["response"])
                            )
                        with measured(timings, "independent_validation_seconds"):
                            validate_tool_response(
                                last_call["name"], last_call["arguments"], result
                            )
                        output = directory / "presentation"
                        phase = "export"
                        writer = ArtifactStore(output)
                        with measured(timings, "result_json_write_seconds"):
                            writer.write(
                                output / "result.json", json_bytes(result.model_dump(mode="json"))
                            )
                        with measured(timings, "public_report_generation_seconds"):
                            published_report = public_report(result, dict(state))
                            report_bytes = published_report.encode("utf-8")
                        summary["report_checks"] = single_report_checks(
                            case, result, published_report
                        )
                        with measured(timings, "public_report_write_seconds"):
                            writer.write(output / "report.md", report_bytes)
                        with measured(timings, "timeline_write_seconds"):
                            writer.write(output / "agent-timeline.json", json_bytes(timeline))
                        with measured(timings, "visualization_generation_seconds"):
                            html_bytes = render_html(
                                result, context=public_context(dict(state)), timeline=timeline
                            ).encode("utf-8")
                        with measured(timings, "visualization_write_seconds"):
                            writer.write(output / "visualization.html", html_bytes)
                        summary["artifacts"] = [
                            "presentation/result.json",
                            "presentation/report.md",
                            "presentation/agent-timeline.json",
                            "presentation/visualization.html",
                        ]
                    visible_errors = public_errors(state["errors"], feedback=state["feedback"])
                    stdout, stderr = task_output(state["status"], published_report, visible_errors)
                    audit.store.write(directory / "user-output.txt", stdout.encode("utf-8"))
                    audit.store.write(directory / "user-errors.txt", stderr.encode("utf-8"))
                    summary["user_visible_output"] = {
                        "stdout_file": "user-output.txt",
                        "stderr_file": "user-errors.txt",
                        "errors": visible_errors,
                        "formatter": "shared_public_cli_task_output",
                        "is_separate_cli_process": False,
                        "report_matches_saved_file": state["status"] == "completed",
                    }
                    rejected = (
                        state["status"] == "failed"
                        and state["feedback"].get("code")
                        in {
                            "UNSUPPORTED_REQUEST",
                            "INVALID_REQUEST_COUNT",
                            "INVALID_CUSTOMER_COUNT",
                        }
                        and not calls
                        and not chosen
                        and summary["correct_initial_tool"] is True
                    )
                    summary["acceptance_passed"] = (
                        rejected
                        if case.tool is None
                        else state["status"] == "completed"
                        and summary["correct_initial_tool"]
                        and summary["correct_scenario"]
                        and axis_ok
                        and all(summary.get("report_checks", {}).values())
                    )
                    summary["semantic_review_required"] = case.id in {
                        "C",
                        "D1",
                        "D2",
                        "E",
                        "F1",
                        "F2",
                    }
                    if (
                        case.tool is not None
                        and state["feedback"].get("code") == "UNSUPPORTED_REQUEST"
                    ):
                        audit.stop("MISSING_TOOL_CALLS_FOR_SUPPORTED_CASE")
    except TimeoutError:
        audit.stop("CASE_TIMEOUT")
        summary["error_type"] = "CASE_TIMEOUT"
    except Exception as exc:
        audit.stop("CASE_RUNTIME_ERROR")
        summary["error_type"] = type(exc).__name__
        summary["failure_stage"] = phase
    summary["seconds"] = time.perf_counter() - start
    timings["remote_http_seconds"] = sum(
        r.get("seconds", 0) for r in audit.data["requests"] if r["case_id"] == case.id
    )
    audit.store.write(directory / "summary.json", json_bytes(summary))
    return summary
