"""Local SDK/HTTP fixtures only; never sends a remote model request."""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from sim2clip.agent.planner import PlannerFailure
from sim2clip.agent.policy import AgentPolicy
from sim2clip.agent.state import initial_state
from sim2clip.config import ModelSettings
from sim2clip.r1 import (
    BASE_URL,
    CASES,
    ENDPOINT,
    MODEL,
    REVERIFY_CASES,
    Audit,
    GuardedTransport,
    Limits,
    RequestBlocked,
    build_model,
    configuration_review,
    cost_cny,
    projected_call,
    run_case,
    safe_arguments,
    single_report_checks,
)

CANARY = "PRIVATE_SECRET_MUST_NOT_BE_EXPORTED"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def settings() -> ModelSettings:
    return ModelSettings.load(
        environ={"MODEL_API_KEY": CANARY, "MODEL_BASE_URL": BASE_URL, "MODEL_NAME": MODEL}
    )


def envelope(case_id: str = "A", *, pilot: bool = False) -> dict[str, Any]:
    request: dict[str, Any] = {
        "experiments": [{"cashiers": c, "customers": 100} for c in (1, 3)],
        "seeds": [42] if pilot else [42, 43, 44, 45, 46],
    }
    if case_id == "B":
        request = {
            "comparison_axis": "server_pool",
            "seeds": [42, 43, 44, 45, 46],
            "experiments": [
                {
                    "group_id": f"group{n}",
                    "config": {
                        "requests": 1000,
                        "seed": 42,
                        "servers": [{"server_id": i + 1} for i in range(n)],
                    },
                }
                for n in (1, 3)
            ],
        }
    if case_id == "G":
        request = {"customers": 20, "cashiers": 2, "seed": 42}
    if case_id == "H":
        request = {
            "requests": 20,
            "servers": [{"server_id": 1}, {"server_id": 2}],
            "routing_strategy": "round_robin",
            "seed": 42,
        }
    return {
        "experiment_goal": CANARY,
        "rationale": CANARY,
        "assumptions": [CANARY],
        "request": request,
    }


def response(
    args: dict[str, Any] | str | None = None, *, name: str = "compare_simulations"
) -> httpx.Response:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": CANARY,
        "reasoning_content": CANARY,
        "vendor_extension": CANARY,
    }
    if args is not None:
        message["tool_calls"] = [
            {
                "id": CANARY,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": CANARY,
            "model": MODEL,
            "object": "chat.completion",
            "created": 1,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if args is not None else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12000,
                "completion_tokens": 1000,
                "total_tokens": 13000,
                "completion_tokens_details": {"reasoning_tokens": 100},
            },
            "vendor_secret": CANARY,
        },
    )


async def plan(audit: Audit, transport: httpx.AsyncBaseTransport, case_id: str = "A") -> Any:
    async with httpx.AsyncClient(transport=GuardedTransport(transport, audit, case_id)) as client:
        planner = build_model(settings(), audit, client)
        # Use the exact shared discovered schemas in this protocol-only probe.
        from mcp.types import Tool

        from sim2clip.scenario_registry import TOOLS

        tools = [
            Tool(
                name=c.name,
                description=c.description,
                input_schema=c.input_model.model_json_schema(),
                output_schema=c.output_model.model_json_schema(mode="serialization"),
            )
            for c in TOOLS.values()
        ]
        return await planner.plan(
            initial_state("比较100位顾客的1/3收银台", "A", AgentPolicy().to_dict()), tools
        )


def test_configuration_source_review_never_prints_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        f"MODEL_API_KEY={CANARY}\nMODEL_BASE_URL={BASE_URL}\nMODEL_NAME={MODEL}\n", encoding="utf-8"
    )
    for name in ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    review = configuration_review(path)
    assert review["configuration_valid"] and review["endpoint_matches"] and review["model_matches"]
    assert review["variables"]["MODEL_API_KEY"]["effective_source"] == ".env"
    monkeypatch.setenv("MODEL_NAME", "wrong-model")
    review = configuration_review(path)
    assert not review["model_matches"]
    assert review["variables"]["MODEL_NAME"]["effective_source"] == "process_environment"
    assert CANARY not in json.dumps(review)
    monkeypatch.setenv("MODEL_API_KEY", "")
    assert not configuration_review(path)["configuration_valid"]


@pytest.mark.parametrize(
    "values",
    [
        {"total_requests": 0},
        {"total_requests": 31},
        {"case_requests": 4},
        {"output_tokens": 4097},
        {"model_seconds": 61},
        {"case_seconds": 241},
        {"request_bytes": 64001},
    ],
)
def test_limits_cannot_exceed_approved_bounds(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Limits(**values)


def test_free_text_and_unknown_values_are_omitted_even_for_invalid_parameters() -> None:
    args = envelope()
    args["request"]["experiments"][0]["customers"] = -5
    args["request"]["group_id"] = CANARY
    args["request"]["extra_sensitive"] = CANARY
    projection = projected_call("compare_simulations", args, 0)
    assert not projection["envelope_and_request_schema_valid"]
    assert projection["args_projection"]["request"]["experiments"][0]["customers"] == -5
    assert CANARY not in json.dumps(projection)
    assert safe_arguments({"seed": float("nan"), "mode": CANARY}) == {
        "seed": "nonfinite_number",
        "mode": "redacted_value",
    }


def test_prices_include_reasoning_only_once_and_account_for_tiers() -> None:
    assert cost_cny(12000, 4096) == pytest.approx(0.0292608)
    assert cost_cny(128001, 10) == pytest.approx((128001 * 2 + 10 * 12) / 1e6)
    assert cost_cny(256001, 10) == pytest.approx((256001 * 4 + 10 * 24) / 1e6)
    with pytest.raises(ValueError):
        cost_cny(-1, 0)


@pytest.mark.anyio
async def test_sdk_wire_cap_native_calls_usage_and_private_text(tmp_path: Path) -> None:
    seen = []

    async def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread())
        seen.append(body)
        assert body["max_completion_tokens"] == 4096 and "max_tokens" not in body
        assert body["enable_thinking"] is False and body["stream"] is False
        assert request.url == ENDPOINT and request.headers["Authorization"] == f"Bearer {CANARY}"
        return response(envelope())

    audit = Audit(tmp_path, Limits(), remote=False)
    answer = await plan(audit, httpx.MockTransport(handle))
    assert len(seen) == 1 and answer.tool_calls[0]["name"] == "compare_simulations"
    assert "request" not in answer.tool_calls[0]["args"]  # existing planner normalization
    assert answer.usage_metadata and answer.usage_metadata["output_tokens"] == 1000
    row = audit.data["requests"][0]
    assert row["http_tool_calls"][0]["arguments_json_valid"]
    assert row["AIMessage.tool_calls"][0]["envelope_and_request_schema_valid"]
    assert row["usage"]["reasoning_tokens_included"] == 100
    assert row["gross_cost_cny"] == pytest.approx(0.0144)
    assert row["seconds"] >= 0 and row["http_status"] == 200
    serialized = audit.path.read_text(encoding="utf-8")
    assert (
        CANARY not in serialized
        and "Authorization" not in serialized
        and "reasoning_content" not in serialized
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind",
    [
        "unauthorized",
        "no_quota",
        "unknown_model",
        "rate_limit",
        "timeout",
        "cancelled",
        "malformed",
    ],
)
async def test_failed_attempt_is_counted_and_stops_more_requests(tmp_path: Path, kind: str) -> None:
    dispatched = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched += 1
        if kind == "timeout":
            raise httpx.ReadTimeout(CANARY)
        if kind == "cancelled":
            await asyncio.sleep(1)
        if kind == "malformed":
            return httpx.Response(200, text=CANARY)
        return httpx.Response(
            {"unauthorized": 401, "no_quota": 403, "unknown_model": 404, "rate_limit": 429}.get(
                kind, 500
            ),
            json={"error": {"message": CANARY}},
        )

    audit = Audit(
        tmp_path, Limits(model_seconds=0.05) if kind == "cancelled" else Limits(), remote=False
    )
    with pytest.raises(PlannerFailure):
        await plan(audit, httpx.MockTransport(handle))
    assert dispatched == 1 and len(audit.data["requests"]) == 1 and audit.data["stop_reason"]
    with pytest.raises(PlannerFailure):
        await plan(audit, httpx.MockTransport(handle))
    assert dispatched == 1
    assert CANARY not in audit.path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_case_limit_and_persisted_total_limit_block_before_dispatch(tmp_path: Path) -> None:
    dispatched = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched += 1
        return response(envelope())

    audit = Audit(tmp_path, Limits(total_requests=2, case_requests=1), remote=False)
    await plan(audit, httpx.MockTransport(handle), "A")
    with pytest.raises(PlannerFailure):
        await plan(audit, httpx.MockTransport(handle), "A")
    assert dispatched == 1
    reloaded = Audit(tmp_path, audit.limits, remote=False)
    await plan(reloaded, httpx.MockTransport(handle), "B")
    with pytest.raises(PlannerFailure):
        await plan(reloaded, httpx.MockTransport(handle), "E")
    assert (
        dispatched == 2 and len(Audit(tmp_path, audit.limits, remote=False).data["requests"]) == 2
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["tokens", "thinking", "model", "endpoint", "body"])
async def test_final_wire_guard_rejects_unapproved_requests(tmp_path: Path, mutation: str) -> None:
    dispatched = []

    async def handle(request: httpx.Request) -> httpx.Response:
        dispatched.append(request)
        return response()

    audit = Audit(tmp_path, Limits(), remote=False)
    body = {
        "model": MODEL,
        "max_completion_tokens": 4096,
        "enable_thinking": False,
        "tools": [
            {"function": {"name": n}}
            for n in (
                "run_simulation",
                "compare_simulations",
                "run_server_simulation",
                "compare_server_simulations",
            )
        ],
    }
    url = ENDPOINT
    if mutation == "tokens":
        body["max_completion_tokens"] = 5000
    elif mutation == "thinking":
        body["enable_thinking"] = True
    elif mutation == "model":
        body["model"] = "other-model"
    elif mutation == "endpoint":
        url += "?secret=" + CANARY
    else:
        body["messages"] = CANARY * 3000
    async with httpx.AsyncClient(
        transport=GuardedTransport(httpx.MockTransport(handle), audit, "A")
    ) as client:
        with pytest.raises(RequestBlocked):
            await client.post(url, json=body)
    assert not dispatched and not audit.data["requests"]


@pytest.mark.anyio
async def test_invalid_native_json_audited_before_existing_planner_replan(tmp_path: Path) -> None:
    audit = Audit(tmp_path, Limits(), remote=False)
    with pytest.raises(PlannerFailure) as exc:
        await plan(audit, httpx.MockTransport(lambda r: response(CANARY)))
    assert exc.value.code == "INVALID_PLAN_JSON" and exc.value.repairable
    row = audit.data["requests"][0]
    assert not row["http_tool_calls"][0]["arguments_json_valid"]
    assert row["invalid_tool_call_count"] == 1
    assert CANARY not in audit.path.read_text(encoding="utf-8")


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", ["A", "B", "E", "G", "H"])
async def test_local_sdk_to_real_stdio_graph_audit_and_report(tmp_path: Path, case_id: str) -> None:
    case = next(c for c in CASES if c.id == case_id)
    audit = Audit(tmp_path, Limits(), remote=False)
    result = await run_case(
        case,
        settings(),
        audit,
        inner=httpx.MockTransport(
            lambda r: response(
                None if case_id == "E" else envelope(case_id),
                name=case.tool or "compare_simulations",
            )
        ),
    )
    assert result["acceptance_passed"] and result["correct_initial_tool"]
    assert audit.data["evidence_type"] == "local_http_fixture"
    if case_id == "E":
        assert (
            result["status"] == "failed"
            and not result["mcp_calls"]
            and "UNSUPPORTED_REQUEST" in result["error_codes"]
        )
    else:
        assert (
            result["status"] == "completed"
            and result["validation"]["valid"]
            and result["validation"]["sufficient"]
        )
        assert result["mcp_calls"][0]["receipt_verified"] and not result["mcp_calls"][0]["cached"]
        assert {n["node"] for n in result["nodes"]} >= {
            "planner",
            "parameter_validator",
            "executor",
            "result_validator",
            "report",
        }
        for name in result["artifacts"]:
            assert CANARY not in (tmp_path / case_id / name).read_text(encoding="utf-8")
        timings = result["timings"]
        assert timings["independent_validation_seconds"] >= 0
        assert timings["public_report_generation_seconds"] >= 0
        assert timings["visualization_generation_seconds"] >= 0
        assert timings["simulation_compute_seconds"] is None
        assert timings["mcp_direct_call_seconds"] is None
        assert timings["checkpoint_node_spans"][0]["seconds"] is None
        if case_id in {"G", "H"}:
            assert all(result["report_checks"].values())
            visible = (tmp_path / case_id / "user-output.txt").read_text(encoding="utf-8")
            assert "任务状态：completed" in visible and "实验目标：描述单次" in visible
            assert (tmp_path / case_id / "presentation/report.md").read_text(
                encoding="utf-8"
            ) in visible
    assert CANARY not in (tmp_path / case_id / "summary.json").read_text(encoding="utf-8")
    assert CANARY not in (tmp_path / case_id / "user-output.txt").read_text(encoding="utf-8")
    assert CANARY not in (tmp_path / case_id / "user-errors.txt").read_text(encoding="utf-8")


def test_remote_entry_requires_authorization_and_valid_real_smoke_proof(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "r1_entry", Path(__file__).parents[2] / "scripts/verify_r1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.parser().parse_args(["smoke", "--output", str(tmp_path / "new")])
    with pytest.raises(ValueError, match="authorization"):
        module.stage(args)
    assert not (tmp_path / "new").exists()
    proof = tmp_path / "fixture-proof.json"
    proof.write_text(
        json.dumps({"stage": "smoke", "evidence_type": "local_http_fixture", "passed": True}),
        encoding="utf-8",
    )
    args = module.parser().parse_args(
        [
            "full",
            "--output",
            str(tmp_path / "new"),
            "--authorize-remote",
            "--smoke-proof",
            str(proof),
        ]
    )
    with pytest.raises(ValueError, match="passed real remote A/B/E"):
        module.stage(args)
    assert not (tmp_path / "new").exists()


@pytest.mark.anyio
async def test_illegal_native_arguments_are_replanned_before_stdio(tmp_path: Path) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        args = envelope()
        if calls == 1:
            args["request"]["experiments"][0]["customers"] = -100
        return response(args)

    audit = Audit(tmp_path, Limits(), remote=False)
    result = await run_case(CASES[0], settings(), audit, inner=httpx.MockTransport(handle))
    assert result["acceptance_passed"] and result["retry_counts"]["parameters"] == 1
    assert not result["first_parameters_accepted"] and len(result["mcp_calls"]) == 1
    assert len(audit.data["requests"]) == 2
    assert not audit.data["requests"][0]["AIMessage.tool_calls"][0][
        "envelope_and_request_schema_valid"
    ]


@pytest.mark.anyio
async def test_supported_query_without_calls_stops_the_suite(tmp_path: Path) -> None:
    audit = Audit(tmp_path, Limits(), remote=False)
    result = await run_case(
        CASES[0], settings(), audit, inner=httpx.MockTransport(lambda r: response())
    )
    assert not result["acceptance_passed"] and not result["mcp_calls"]
    assert audit.data["stop_reason"] == "MISSING_TOOL_CALLS_FOR_SUPPORTED_CASE"


def test_outer_watchdog_records_failure_and_skips_remaining_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    spec = importlib.util.spec_from_file_location(
        "r1_watchdog", Path(__file__).parents[2] / "scripts/verify_r1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "configuration_review",
        lambda p: {"configuration_valid": True, "model_matches": True, "endpoint_matches": True},
    )

    class HangingChild:
        returncode: int | None = None

        def wait(self, timeout: float) -> None:
            raise subprocess.TimeoutExpired("local-fixture", timeout)

    child = HangingChild()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(module, "stop_child", lambda c: setattr(c, "returncode", -1))
    args = module.parser().parse_args(
        ["smoke", "--authorize-remote", "--output", str(tmp_path / "new")]
    )
    result = module.stage(args)
    assert not result["passed"] and result["not_run"] == ["B", "E"]
    assert result["request_count"] == 0 and result["stop_reason"] == "CASE_WATCHDOG_TIMEOUT"


@pytest.mark.anyio
async def test_smoke_six_request_limit_counts_all_three_case_allocations(tmp_path: Path) -> None:
    audit = Audit(tmp_path, Limits(), remote=False)
    transport = httpx.MockTransport(lambda request: response(envelope()))
    for case_id in ("A", "A", "B", "B", "E", "E"):
        await plan(audit, transport, case_id)
    with pytest.raises(PlannerFailure):
        await plan(audit, transport, "C")
    assert len(audit.data["requests"]) == 6
    assert audit.data["stop_reason"] == "TOTAL_REQUEST_LIMIT"


def test_watchdog_terminates_only_its_own_local_child() -> None:
    import subprocess
    import sys

    spec = importlib.util.spec_from_file_location(
        "r1_termination", Path(__file__).parents[2] / "scripts/verify_r1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        module.stop_child(child)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_full_stage_passes_fourteen_total_two_per_case_before_any_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "r1_full_limits", Path(__file__).parents[2] / "scripts/verify_r1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "configuration_review",
        lambda p: {"configuration_valid": True, "model_matches": True, "endpoint_matches": True},
    )
    proof = tmp_path / "synthetic-proof-not-remote-evidence.json"
    proof.write_text(
        json.dumps(
            {
                "stage": "smoke",
                "evidence_type": "real_remote",
                "passed": True,
                "cases": [{"case_id": c, "acceptance_passed": True} for c in ("A", "B", "E")],
            }
        ),
        encoding="utf-8",
    )
    captured: list[str] = []

    def intercepted(argv: list[str], **kwargs: object) -> None:
        captured.extend(argv)
        raise RuntimeError("LOCAL_INTERCEPT_BEFORE_CHILD")

    monkeypatch.setattr(module.subprocess, "Popen", intercepted)
    args = module.parser().parse_args(
        [
            "full",
            "--authorize-remote",
            "--output",
            str(tmp_path / "new"),
            "--smoke-proof",
            str(proof),
        ]
    )
    with pytest.raises(RuntimeError, match="LOCAL_INTERCEPT_BEFORE_CHILD"):
        module.stage(args)
    assert captured[captured.index("--case") + 1] == "C"
    assert captured[captured.index("--total-requests") + 1] == "14"
    assert captured[captured.index("--case-requests") + 1] == "2"
    assert not json.loads((tmp_path / "new" / "audit.json").read_text())["requests"]
    with pytest.raises(ValueError):
        Limits(total_requests=15)
    with pytest.raises(ValueError):
        Limits(case_requests=3)


@pytest.mark.anyio
async def test_full_fourteen_request_limit_is_durable_across_seven_case_allocations(
    tmp_path: Path,
) -> None:
    audit = Audit(tmp_path, Limits(total_requests=14, case_requests=2), remote=False)
    transport = httpx.MockTransport(lambda request: response(envelope()))
    for case_id in ("C", "D1", "D2", "F1", "F2", "G", "H"):
        for _ in range(2):
            audit = Audit(tmp_path, audit.limits, remote=False)
            await plan(audit, transport, case_id)
    with pytest.raises(PlannerFailure):
        await plan(audit, transport, "H")
    assert len(audit.data["requests"]) == 14
    assert audit.data["stop_reason"] == "TOTAL_REQUEST_LIMIT"


def test_reverify_requires_new_authorization_and_real_complete_proof(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "r1_reverify", Path(__file__).parents[2] / "scripts/verify_r1.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proof = tmp_path / "synthetic-proof-not-remote-evidence.json"
    proof.write_text(
        json.dumps({"stage": "full", "evidence_type": "local_http_fixture", "passed": True})
    )
    argv = ["reverify", "--output", str(tmp_path / "new"), "--r1-proof", str(proof)]
    with pytest.raises(ValueError, match="authorization"):
        module.stage(module.parser().parse_args(argv))
    with pytest.raises(ValueError, match="real R1"):
        module.stage(module.parser().parse_args([*argv, "--authorize-remote"]))
    assert not (tmp_path / "new").exists()
    assert [(case.id, case.query) for case in REVERIFY_CASES] == [
        ("G", "用2个收银台模拟20位顾客排队。"),
        ("H", "用两台服务器和Round Robin策略模拟20个请求。"),
    ]
    proof.write_text(
        json.dumps(
            {
                "stage": "full",
                "evidence_type": "real_remote",
                "passed": True,
                "cases": [
                    {"case_id": case.id, "acceptance_passed": True}
                    for case in CASES
                    if case.id not in {"A", "B", "E"}
                ],
            }
        )
    )
    monkeypatch.setattr(
        module,
        "configuration_review",
        lambda p: {
            "configuration_valid": True,
            "model_matches": True,
            "endpoint_matches": True,
        },
    )
    captured = []

    class InterceptedChild:
        returncode = 0

        def __init__(self, args, **kwargs):
            captured.append(args)
            case_id = args[args.index("--case") + 1]
            root = Path(args[args.index("--output") + 1])
            folder = root / case_id
            folder.mkdir()
            (folder / "summary.json").write_text(
                json.dumps({"case_id": case_id, "status": "completed", "acceptance_passed": True})
            )

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", InterceptedChild)
    result = module.stage(module.parser().parse_args([*argv, "--authorize-remote"]))
    assert result["request_limit"] == 4
    assert [args[args.index("--case") + 1] for args in captured] == ["G", "H"]
    assert all(args[args.index("--total-requests") + 1] == "4" for args in captured)
    assert all(args[args.index("--case-requests") + 1] == "2" for args in captured)
    assert all("--reverify" in args for args in captured)
    assert not json.loads((tmp_path / "new/audit.json").read_text())["requests"]


@pytest.mark.anyio
async def test_four_request_reverify_budget_counts_failed_dispatches(tmp_path):
    audit = Audit(tmp_path, Limits(total_requests=4, case_requests=2), remote=False)
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic request failure")

    for case_id in ("G", "G", "H", "H"):
        # Inspect persisted failed count; production stop_reason is never cleared.
        audit = Audit(tmp_path, audit.limits, remote=False)
        if audit.data["stop_reason"]:
            break
        with pytest.raises(PlannerFailure):
            await plan(audit, httpx.MockTransport(fail), case_id)
    assert len(calls) == 1 and len(audit.data["requests"]) == 1
    assert audit.data["requests"][0]["error_type"] == "TimeoutException"
    with pytest.raises(PlannerFailure):
        await plan(audit, httpx.MockTransport(fail), "H")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_four_request_reverify_budget_is_durable(tmp_path):
    audit = Audit(tmp_path, Limits(total_requests=4, case_requests=2), remote=False)
    transport = httpx.MockTransport(lambda request: response(envelope("G"), name="run_simulation"))
    for case_id in ("G", "G", "H", "H"):
        audit = Audit(tmp_path, audit.limits, remote=False)
        await plan(audit, transport, case_id)
    with pytest.raises(PlannerFailure):
        await plan(audit, transport, "H")
    assert len(audit.data["requests"]) == 4
    assert audit.data["stop_reason"] == "TOTAL_REQUEST_LIMIT"


def test_single_report_acceptance_detects_old_comparison_goal_and_wrong_counts():
    from sim2clip.simulation.queue_model import run_simulation
    from sim2clip.simulation.schemas import SimulationConfig
    from sim2clip.visualization.public import public_report

    case = REVERIFY_CASES[0]
    result = run_simulation(SimulationConfig(customers=20, cashiers=2))
    report = public_report(result, {})
    assert all(single_report_checks(case, result, report).values())
    damaged = report.replace("实验目标：描述单次", "实验目标：比较")
    assert not single_report_checks(case, result, damaged)["public_goal_is_single"]
    assert not single_report_checks(case, result, damaged)["no_invented_comparison"]
    wrong = run_simulation(SimulationConfig(customers=20, cashiers=1))
    assert not single_report_checks(case, wrong, public_report(wrong, {}))[
        "parameters_match_requested_single"
    ]
