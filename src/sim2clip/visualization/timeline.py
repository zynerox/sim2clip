"""Read node completion checkpoints; never infer missing wall-clock times."""

import re
from datetime import datetime
from typing import Any

from sim2clip.scenario_registry import TOOLS

NODE_NAMES = {
    "planner": ("Planner", "planning"),
    "parameter_validator": ("Parameter Validator", "validation"),
    "parameters": ("Parameter Validator", "validation"),
    "executor": ("MCP Tool Call", "tool"),
    "results": ("Result Validator", "validation"),
    "result_validator": ("Result Validator", "validation"),
    "report": ("Report", "report"),
}


def public_call(call: dict[str, Any]) -> dict[str, Any]:
    """Publish contract fields, never provider IDs, errors or unknown parameters."""
    name = call.get("name")
    if not isinstance(name, str) or name not in TOOLS:
        return {}
    try:
        arguments = (
            TOOLS[name]
            .parse_request(call.get("arguments", {}))
            .model_dump(mode="json", exclude_unset=True)
        )
    except ValueError:
        arguments = {}
    identifier = call.get("id", "")
    return {
        "id": identifier
        if isinstance(identifier, str) and re.fullmatch(r"r\d+-c\d+", identifier)
        else "",
        "name": name,
        "arguments": arguments,
        "attempt": call.get("attempt") if type(call.get("attempt")) is int else None,
        "round": call.get("round") if type(call.get("round")) is int else None,
        "status": call.get("status") if call.get("status") in {"success", "error"} else "error",
        "cached": call.get("cached") is True,
    }


def public_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def public_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allow-list the public view even when callers supply an old saved timeline."""
    projected: list[dict[str, Any]] = []
    for row in rows:
        node = row.get("node") or next(
            (key for key, (name, _) in NODE_NAMES.items() if name == row.get("name")), None
        )
        if node not in NODE_NAMES:
            continue
        name, category = NODE_NAMES[node]
        action = row.get("action")
        if action not in {
            "propose",
            "execute",
            "tool_success",
            "cached",
            "replan",
            "retry",
            "retry_tool",
            "fail",
            "stop",
            "complete",
            "report",
            "accept",
        }:
            action = "recorded"
        projected.append(
            {
                "sequence": len(projected),
                "node": node,
                "name": name,
                "category": category,
                "timestamp": public_timestamp(row.get("timestamp")),
                "checkpoint_step": row.get("checkpoint_step")
                if type(row.get("checkpoint_step")) is int
                else None,
                "round": row.get("round") if type(row.get("round")) is int else None,
                "action": action,
                "status": row.get("status")
                if row.get("status") in {"retry", "failed", "completed"}
                else "failed",
                "reason": f"{name}: {action}; see validated tool parameters and evidence.",
                "tool_calls": [
                    clean for call in row.get("tool_calls", []) if (clean := public_call(call))
                ],
            }
        )
    return projected


def execution_timeline(
    history: list[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """History is chronological: {timestamp, step, state} from saved snapshots."""
    rows: list[dict[str, Any]] = []
    decisions_seen, calls_seen = 0, 0
    pending_calls = list(state.get("tool_calls", []))
    sources = history or [{"timestamp": None, "step": None, "state": state}]
    for snapshot in sources:
        values = snapshot["state"]
        decisions = values.get("decision_history", [])
        calls = values.get("tool_calls", [])
        added_calls = calls[calls_seen:]
        for decision in decisions[decisions_seen:]:
            node = decision["node"]
            name, category = NODE_NAMES.get(node, (node, "validation"))
            action = decision["action"]
            row: dict[str, Any] = {
                "sequence": len(rows),
                "node": node,
                "name": name,
                "category": category,
                "timestamp": snapshot["timestamp"],
                "checkpoint_step": snapshot["step"],
                "round": decision["round"],
                "action": action,
                "status": "retry"
                if action in {"replan", "retry", "retry_tool"}
                else "failed"
                if action in {"fail", "stop"}
                else "completed",
                "reason": decision["reason"],
                "tool_calls": [],
            }
            if node == "executor":
                if not history:
                    matched = next(
                        (
                            index
                            for index, call in enumerate(pending_calls)
                            if call.get("round") == decision["round"]
                        ),
                        None,
                    )
                    added_calls = [] if matched is None else [pending_calls.pop(matched)]
                row["tool_calls"] = [
                    {
                        key: call.get(key)
                        for key in ("id", "name", "arguments", "attempt", "status", "cached")
                    }
                    for call in added_calls
                ]
                if any(call.get("status") != "success" for call in added_calls):
                    row["status"] = "retry" if action != "stop" else "failed"
            rows.append(row)
        decisions_seen, calls_seen = len(decisions), len(calls)
    return public_rows(rows)
