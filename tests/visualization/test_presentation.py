"""Public provenance projection and absolute presentation clock contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sim2clip.agent.artifacts import ArtifactStore
from sim2clip.scenarios.server_contracts import ServerSimulationConfig
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.visualization.html import render_html
from sim2clip.visualization.presentation import PresentationConfig, build_manifest, state_at


@pytest.mark.parametrize(
    "p,phase,t",
    [
        (0, "question", 0),
        (2.999, "question", 0),
        (3, "plan", 0),
        (6, "tool", 0),
        (8, "verify", 0),
        (10, "simulate", 0),
        (17, "simulate", 50),
        (23.999, "simulate", 99.99285714285714),
        (24, "reveal", 100),
        (30, "reveal", 100),
        (99, "reveal", 100),
        (-1, "question", 0),
    ],
)
def test_phase_boundaries_and_shared_mapping(p, phase, t):
    observed, clock, reveal = state_at(PresentationConfig(), p, 100)
    assert observed == phase
    assert clock == pytest.approx(t)
    assert reveal == (phase == "reveal")
    assert not state_at(PresentationConfig(), p, 100, valid=False)[2]


def test_configuration_is_central_and_validated():
    config = PresentationConfig(duration_seconds=60, phase_durations=(6, 6, 4, 4, 28, 12))
    assert state_at(config, 34, 100) == ("simulate", 50, False)
    for changes in ({"phase_durations": (0, 3, 2, 2, 17, 6)}, {"duration_seconds": 31}):
        with pytest.raises(ValidationError):
            PresentationConfig(**changes)


def evidence_state(result, tmp_path: Path):
    store = ArtifactStore(tmp_path)
    name = "run_server_simulation" if hasattr(result.config, "requests") else "run_simulation"
    call = {
        "id": "r1-c0",
        "name": name,
        "arguments": result.config.model_dump(mode="json"),
        "round": 1,
        "attempt": 1,
        "status": "success",
        "cached": False,
    }
    call["response"] = store.save_response("test", call, result.model_dump(mode="json"))
    rows = [
        {
            "node": n,
            "round": 1,
            "action": a,
            "status": "completed",
            "tool_calls": [call] if n == "executor" else [],
        }
        for n, a in [
            ("planner", "propose"),
            ("executor", "tool_success"),
            ("result_validator", "report"),
        ]
    ]
    state = {
        "tool_calls": [call],
        "planning_round": 1,
        "planner_mode": "llm",
        "validation_result": {"valid": True, "sufficient": True, "checks": ["metrics"]},
    }
    return store, state, rows


def test_receipt_linkage_unknown_history_and_sensitive_allowlist(hand_result, tmp_path):
    # supplied queue samples are legal saved evidence but not a generated MCP response.
    from sim2clip.simulation import SimulationConfig, run_simulation

    result = run_simulation(SimulationConfig(customers=3, cashiers=1))
    store, state, rows = evidence_state(result, tmp_path)
    secret = "sk-PRIVATE-reason-message-content"
    state.update(user_query=secret, experiment_goal=secret, messages=[secret])
    rows[0]["reason"] = secret
    state["validation_result"]["checks"].append(secret)
    manifest = build_manifest(result, state=state, store=store, timeline=rows)
    assert secret not in manifest.model_dump_json() + render_html(result, presentation=manifest)
    assert manifest.execution.planner_kind == "real_llm"
    assert manifest.execution.evidence_completeness == "complete"
    assert manifest.execution.calls[0].association == "verified"
    assert [v.origin for v in manifest.execution.verification] == ["agent", "export"]
    unknown = build_manifest(result, timeline=rows)
    assert unknown.execution.planner_kind == "unknown"
    assert unknown.execution.calls[0].association == "unverified"
    only = build_manifest(result)
    assert only.execution.evidence_completeness == "result_only"
    assert not only.execution.calls
    assert [v.origin for v in only.execution.verification] == ["export"]
    wrong = build_manifest(hand_result, state=state, store=store, timeline=rows)
    assert wrong.execution.calls[0].association == "unverified"
    assert len(wrong.execution.verification) == 1
    with pytest.raises(ValueError, match="different result"):
        render_html(hand_result, presentation=manifest)
    state["tool_calls"][0]["status"] = "error"
    failed = build_manifest(result, state=state, store=store, timeline=rows)
    assert failed.execution.calls[0].status == "error"
    assert failed.execution.calls[0].association == "unverified"
    assert json.loads(failed.model_dump_json())["result_ref"]["result_id"] == result.result_id


def test_server_locked_legal_primary_metric_and_explicit_public_question(tmp_path):
    result = run_server_simulation(ServerSimulationConfig(requests=5))
    store, state, rows = evidence_state(result, tmp_path)
    state["design"] = {"primary_metric": "p95_response_time", "reason": "PRIVATE"}
    manifest = build_manifest(
        result, state=state, store=store, timeline=rows, public_question="这五个请求的 P95 是多少？"
    )
    assert manifest.execution.primary_metric == "p95_response_time"
    assert manifest.execution.question_source == "explicit_public"
    assert "PRIVATE" not in manifest.model_dump_json()
