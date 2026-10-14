"""Meaningful state transitions: planning, validation, MCP execution and reporting."""

import json

from langchain_core.messages import HumanMessage, ToolMessage, messages_to_dict
from pydantic import ValidationError

from sim2clip.agent.artifacts import ArtifactStore, json_bytes
from sim2clip.agent.checkpoint_compat import compatibility_fields, compatible_state
from sim2clip.agent.mcp_client import SimulationMCPClient, ToolFailure, parse_result
from sim2clip.agent.planner import Planner, PlannerFailure
from sim2clip.agent.query_validation import (
    UnsupportedQuery,
    check_explicit_comparison,
    check_explicit_population,
    check_query_scope,
)
from sim2clip.agent.scenario_adapters import (
    check_design_lock,
    compact_response,
    comparison_covers,
    comparison_reference,
    experiment_design,
    is_comparison,
    minimum_seeds,
    validated_request,
)
from sim2clip.agent.state import JSONDict, SimulationAgentState
from sim2clip.scenario_registry import (
    SCENARIOS,
    ComparisonOutput,
    SimulationOutput,
    result_runs,
    tool_contract,
    validate_tool_response,
)
from sim2clip.scenarios.server_contracts import ServerComparisonResult
from sim2clip.simulation.schemas import ComparisonResult


def decision(
    state: SimulationAgentState,
    node: str,
    action: str,
    reason: str,
    *,
    round_number: int | None = None,
) -> list[JSONDict]:
    return [
        {
            "node": node,
            "round": state["planning_round"] if round_number is None else round_number,
            "action": action,
            "reason": reason,
        }
    ]


def failure(
    state: SimulationAgentState, node: str, message: str, *, code: str = "WORKFLOW_ERROR"
) -> JSONDict:
    return {
        "status": "failed",
        "next_action": "end",
        "errors": [{"node": node, "code": code, "message": message}],
        "decision_history": decision(state, node, "stop", message),
    }


def replan(state: SimulationAgentState, category: str, feedback: JSONDict) -> JSONDict:
    counts = dict(state["retry_counts"])
    policy_key = "parameter_replans" if category == "parameters" else "result_replans"
    if counts[category] >= state["policy"][policy_key]:
        return {**failure(state, category, "Replanning budget exhausted"), "feedback": feedback}
    counts[category] += 1
    return {
        "retry_counts": counts,
        "feedback": feedback,
        "status": "replanning",
        "next_action": "planner",
        "decision_history": decision(state, category, "replan", feedback["reason"]),
    }


def tool_message(call: JSONDict, payload: JSONDict, *, error: bool = False) -> list[JSONDict]:
    return messages_to_dict(
        [
            ToolMessage(
                content=json.dumps(payload, ensure_ascii=False),
                tool_call_id=call["id"],
                status="error" if error else "success",
            )
        ]
    )


class AgentNodes:
    def __init__(self, planner: Planner, mcp: SimulationMCPClient, store: ArtifactStore) -> None:
        self.planner, self.mcp, self.store = planner, mcp, store

    def verify_completed(self, state: SimulationAgentState) -> None:
        """Completed resumes still require intact evidence, without another execution."""
        if not state["validation_result"].get("valid") or not state["validation_result"].get(
            "sufficient"
        ):
            raise ValueError("Completed checkpoint has no valid, sufficient evidence")
        current = [
            item
            for item in state["tool_calls"]
            if item["status"] == "success" and item["round"] == state["planning_round"]
        ]
        self._verified_evidence(state, current)

    async def plan(self, state: SimulationAgentState) -> JSONDict:
        state = compatible_state(state)
        return {**compatibility_fields(state), **await self._plan(state)}

    def parameters(self, state: SimulationAgentState) -> JSONDict:
        state = compatible_state(state)
        return {**compatibility_fields(state), **self._parameters(state)}

    async def execute(self, state: SimulationAgentState) -> JSONDict:
        state = compatible_state(state)
        return {**compatibility_fields(state), **await self._execute(state)}

    def results(self, state: SimulationAgentState) -> JSONDict:
        state = compatible_state(state)
        return {**compatibility_fields(state), **self._results(state)}

    def report(self, state: SimulationAgentState) -> JSONDict:
        state = compatible_state(state)
        return {**compatibility_fields(state), **self._report(state)}

    async def _plan(self, state: SimulationAgentState) -> JSONDict:
        round_number = state["planning_round"] + 1
        try:
            answer = await self.planner.plan(state, self.mcp.tools)
            calls: list[JSONDict] = []
            for index, raw in enumerate(answer.tool_calls):
                internal_id = f"r{round_number}-c{index}"
                calls.append(
                    {
                        "id": internal_id,
                        "provider_id": raw.get("id"),
                        "name": raw["name"],
                        "arguments": raw["args"],
                    }
                )
                raw["id"] = internal_id
            explanation = (
                answer.content
                if isinstance(answer.content, str)
                else json.dumps(answer.content, ensure_ascii=False)
            )
            messages = messages_to_dict([answer])
            if not state["messages"]:
                messages = messages_to_dict([HumanMessage(content=state["user_query"])]) + messages
            return {
                "experiment_goal": (
                    state["experiment_goal"]
                    if state["design"]
                    else answer.additional_kwargs.get("planning_metadata", {}).get(
                        "experiment_goal", state["user_query"]
                    )
                ),
                "experiment_plan": {
                    "reason": explanation,
                    "calls": calls,
                    "metadata": answer.additional_kwargs.get("planning_metadata", {}),
                },
                "planning_round": round_number,
                "planner_mode": self.planner.mode,
                "messages": messages,
                "status": "planning",
                "decision_history": decision(
                    state, "planner", "propose", explanation, round_number=round_number
                ),
            }
        except PlannerFailure as exc:
            feedback = {"code": exc.code, "reason": str(exc)}
            attempted = state.copy()
            attempted["planning_round"] = round_number
            update = (
                replan(attempted, "parameters", feedback)
                if exc.repairable
                else failure(attempted, "planner", str(exc))
            )
            return {
                **update,
                "errors": [
                    {"node": "planner", "code": exc.code, "message": str(exc)},
                ],
                "planning_round": round_number,
                "planner_mode": self.planner.mode,
                "feedback": feedback,
            }
        except Exception as exc:
            # No fabricated fallback plan on a model/provider failure.
            return {
                **failure(
                    state,
                    "planner",
                    f"Planner failed: {type(exc).__name__}: {exc}",
                    code="RUNTIME_ERROR",
                ),
                "planning_round": round_number,
                "planner_mode": self.planner.mode,
            }

    def _parameters(self, state: SimulationAgentState) -> JSONDict:
        raw_calls: list[JSONDict] = state["experiment_plan"].get("calls", [])
        try:
            if not 1 <= len(raw_calls) <= state["policy"]["maximum_batch_calls"]:
                raise ValueError(
                    "Planner must select a bounded, nonempty batch of simulation tools"
                )
            calls: list[JSONDict] = []
            for raw in raw_calls:
                arguments, cost = validated_request(raw["name"], raw["arguments"])
                calls.append({**raw, "arguments": arguments, "customer_runs": cost})
            goal = (
                state["experiment_plan"]
                .get("metadata", {})
                .get("experiment_goal", state["experiment_goal"])
            )
            design = experiment_design(calls, goal)
            scene = tool_contract(calls[0]["name"]).scenario_id
            check_query_scope(state["user_query"], scene)
            population = design["workload"]["customers" if scene == "queue" else "requests"]
            check_explicit_population(state["user_query"], population, scene)
            check_explicit_comparison(state["user_query"], design)
            if any(word in state["user_query"].lower() for word in ("比较", "compare", " vs ")):
                if not is_comparison(design):
                    raise ValueError(
                        "User requested a comparison; at least two experiment groups required"
                    )
            previous = state["design"]
            if previous is not None:
                check_design_lock(previous, design)
            remaining = state["policy"]["maximum_customer_runs"] - state["customer_runs"]
            if sum(call["customer_runs"] for call in calls) > remaining:
                raise ValueError("Plan exceeds the remaining customer-run budget")
            if len(state["tool_calls"]) + len(calls) > state["policy"]["maximum_tool_calls"]:
                raise ValueError("Plan exceeds the remaining tool-call budget")
        except UnsupportedQuery as exc:
            return {
                **failure(state, "parameter_validator", str(exc)),
                "feedback": {"code": "UNSUPPORTED_REQUEST", "reason": str(exc)},
                "messages": [
                    reply
                    for call in raw_calls
                    for reply in tool_message(
                        call, {"code": "UNSUPPORTED_REQUEST", "reason": str(exc)}, error=True
                    )
                ],
            }
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            feedback = {"code": "INVALID_PARAMETERS", "reason": str(exc)}
            replies = [
                reply for call in raw_calls for reply in tool_message(call, feedback, error=True)
            ]
            return {**replan(state, "parameters", feedback), "messages": replies}
        return {
            "validated_request": calls,
            "scenario_id": scene,
            "schema_version": "1",
            "experiment_goal": goal,
            "design": design,
            "call_cursor": 0,
            "status": "executing",
            "next_action": "executor",
            "decision_history": decision(
                state,
                "parameter_validator",
                "execute",
                "Shared schemas, fair workload and work budgets validated",
            ),
        }

    async def _execute(self, state: SimulationAgentState) -> JSONDict:
        call = state["validated_request"][state["call_cursor"]]
        attempt = sum(item["id"] == call["id"] for item in state["tool_calls"]) + 1
        history: JSONDict = {
            **call,
            "attempt": attempt,
            "round": state["planning_round"],
            "scenario_id": state["scenario_id"],
            "schema_version": "1",
        }
        if (
            len(state["tool_calls"]) >= state["policy"]["maximum_tool_calls"]
            or state["customer_runs"] + call["customer_runs"]
            > state["policy"]["maximum_customer_runs"]
        ):
            return failure(state, "executor", "Execution budget exhausted")
        try:
            # A receipt can survive a crash before this node's checkpoint is saved.
            # Reuse a matching, hash-checked response; corrupted evidence must fail.
            reference = self.store.cached(state["run_id"], call)
            cached = reference is not None
            if reference is None:
                payload = await self.mcp.call(call["name"], call["arguments"])
                # Save the response and receipt before returning the State update.
                # A crash before the receipt is written can still repeat computation.
                reference = self.store.save_response(state["run_id"], call, payload)
            result = parse_result(call["name"], self.store.load(reference))
        except ToolFailure as exc:
            history.update(status="error", error=exc.to_dict())
            counts = dict(state["retry_counts"])
            update: JSONDict = {
                "tool_calls": [history],
                "customer_runs": state["customer_runs"] + call["customer_runs"],
            }
            if exc.retryable and counts["tools"] < state["policy"]["tool_retries"]:
                counts["tools"] += 1
                if exc.code == "TRANSPORT_ERROR":
                    try:
                        await self.mcp.reconnect()
                    except Exception:
                        return {**update, **failure(state, "executor", "MCP reconnect failed")}
                return {
                    **update,
                    "retry_counts": counts,
                    "next_action": "executor",
                    "decision_history": decision(state, "executor", "retry", exc.code),
                }
            if exc.code == "INVALID_PARAMETERS":
                # Complete the entire AI tool-call batch before another model turn.
                pending = state["validated_request"][state["call_cursor"] :]
                replies = [
                    reply
                    for item in pending
                    for reply in tool_message(item, exc.to_dict(), error=True)
                ]
                return {
                    **update,
                    **replan(state, "parameters", {"code": exc.code, "reason": str(exc)}),
                    "messages": replies,
                }
            return {
                **update,
                **failure(state, "executor", f"{exc.code}: {exc}", code=exc.code),
                "messages": tool_message(call, exc.to_dict(), error=True),
            }
        except (OSError, ValueError, ValidationError) as exc:
            history.update(status="error", error={"code": "ARTIFACT_ERROR", "message": str(exc)})
            return {
                **failure(
                    state, "executor", f"Artifact/result error: {exc}", code="ARTIFACT_ERROR"
                ),
                "tool_calls": [history],
            }
        runs = result_runs(result)
        # Start a new batch projection; earlier receipts remain in tool_calls.
        # Versioned result IDs can differ even for the same Seed and samples.
        references = (
            {}
            if state["call_cursor"] == 0
            else {item["result_id"]: item for item in state["simulation_results"]}
        )
        for run in runs:
            references[run.result_id] = {
                **reference,
                "result_id": run.result_id,
                "config": run.config.model_dump(mode="json"),
                "metrics": run.metrics.model_dump(mode="json"),
                "scenario_id": state["scenario_id"],
                "schema_version": "1",
                "tool_name": call["name"],
            }
            if isinstance(result, ServerComparisonResult):
                references[run.result_id]["group_id"] = next(
                    item.group_id
                    for item in result.results
                    if item.result.result_id == run.result_id
                )
        comparison = None if state["call_cursor"] == 0 else state["comparison_result"]
        candidate = comparison_reference(result, reference)
        if candidate is not None:
            comparison = {
                **candidate,
                "scenario_id": state["scenario_id"],
                "schema_version": "1",
                "tool_name": call["name"],
            }
        compact = compact_response(result, reference)
        cursor = state["call_cursor"] + 1
        history.update(
            status="success",
            response=reference,
            cached=cached,
            result_ids=[run.result_id for run in runs],
        )
        return {
            "tool_calls": [history],
            "simulation_results": list(references.values()),
            "comparison_result": comparison,
            "call_cursor": cursor,
            "customer_runs": state["customer_runs"] + call["customer_runs"],
            "messages": tool_message(call, compact),
            "next_action": "executor"
            if cursor < len(state["validated_request"])
            else "result_validator",
            "decision_history": decision(
                state, "executor", "cached" if cached else "tool_success", call["name"]
            ),
        }

    def _results(self, state: SimulationAgentState) -> JSONDict:
        current = [
            item
            for item in state["tool_calls"]
            if item["round"] == state["planning_round"] and item["status"] == "success"
        ]
        try:
            evidence = self._verified_evidence(state, current)
            comparisons: list[ComparisonOutput] = [
                result
                for result in evidence
                if isinstance(result, (ComparisonResult, ServerComparisonResult))
            ]
        except (ValueError, OSError, KeyError) as exc:
            feedback: JSONDict = {"code": "INVALID_EVIDENCE", "reason": str(exc)}
            return {
                **replan(state, "results", feedback),
                "validation_result": {"valid": False, "sufficient": False, **feedback},
            }
        design = state["design"] or {}
        minimum = minimum_seeds(design, state["policy"]["minimum_comparison_seeds"])
        if is_comparison(design):
            complete = bool(comparisons) and comparison_covers(comparisons[-1], design)
            if not complete or len(comparisons[-1].seeds) < minimum:
                feedback = {
                    "code": "INSUFFICIENT_EVIDENCE",
                    "reason": f"Comparison requires at least {minimum} paired seeds",
                    "minimum_seeds": minimum,
                }
                return {
                    **replan(state, "results", feedback),
                    "validation_result": {"valid": True, "sufficient": False, **feedback},
                }
        validated = {
            "valid": True,
            "sufficient": True,
            "checked_calls": len(current),
            "checks": [
                "event_lifecycle",
                "FIFO",
                "capacity",
                "metrics",
                "request_provenance",
                "paired_samples",
                "comparison_statistics",
            ],
            "scope": "Descriptive finite-population results; no significance or real-world claim",
        }
        return {
            "validation_result": validated,
            "status": "validating",
            "next_action": "report",
            "decision_history": decision(
                state,
                "result_validator",
                "report",
                "Evidence is internally consistent and meets the declared seed policy",
            ),
        }

    def _verified_evidence(
        self, state: SimulationAgentState, calls: list[JSONDict]
    ) -> list[SimulationOutput]:
        """Check durable receipts, locked requests and compact State against raw evidence."""
        if not calls or state["design"] is None:
            raise ValueError("No successful tool evidence or locked design")
        work = 0
        for item in state["tool_calls"]:
            arguments, cost = validated_request(item["name"], item["arguments"])
            if arguments != item["arguments"] or cost != item["customer_runs"]:
                raise ValueError("Tool history has an incorrect normalized request or work cost")
            work += cost
        if (
            work != state["customer_runs"]
            or work > state["policy"]["maximum_customer_runs"]
            or len(state["tool_calls"]) > state["policy"]["maximum_tool_calls"]
        ):
            raise ValueError("Persisted tool evidence violates the experiment budget")
        expected_calls = {call["id"]: call for call in state["validated_request"]}
        if {call["id"] for call in calls} != set(expected_calls):
            raise ValueError("Tool history does not cover the validated request batch")
        observed = experiment_design(calls, state["experiment_goal"])
        check_design_lock(state["design"], observed)
        if observed["seeds"] != state["design"]["seeds"]:
            raise ValueError("Tool evidence does not cover every locked seed")
        evidence: list[SimulationOutput] = []
        expected_refs: JSONDict = {}
        expected_comparison: JSONDict | None = None
        expected_comparison_tool: str | None = None
        for call in calls:
            expected = expected_calls[call["id"]]
            if any(call[key] != expected[key] for key in ("name", "arguments", "customer_runs")):
                raise ValueError("Tool history disagrees with validated request")
            scene = tool_contract(call["name"]).scenario_id
            if scene != state["scenario_id"] or call.get("scenario_id", scene) != scene:
                raise ValueError("Tool evidence conflicts with locked scenario")
            response = call["response"]
            if self.store.cached(state["run_id"], call) != response:
                raise ValueError("Tool response reference disagrees with durable receipt")
            result = parse_result(call["name"], self.store.load(response))
            validate_tool_response(call["name"], call["arguments"], result)
            runs = result_runs(result)
            if call["result_ids"] != [run.result_id for run in runs]:
                raise ValueError("Tool history result identities disagree with evidence")
            for run in runs:
                expected_refs[run.result_id] = {
                    **response,
                    "result_id": run.result_id,
                    "config": run.config.model_dump(mode="json"),
                    "metrics": run.metrics.model_dump(mode="json"),
                }
                if isinstance(result, ServerComparisonResult):
                    expected_refs[run.result_id]["group_id"] = next(
                        item.group_id
                        for item in result.results
                        if item.result.result_id == run.result_id
                    )
            candidate = comparison_reference(result, response)
            if candidate is not None:
                expected_comparison = candidate
                expected_comparison_tool = call["name"]
            evidence.append(result)
        actual_refs = {item["result_id"]: item for item in state["simulation_results"]}
        if set(actual_refs) != set(expected_refs) or len(actual_refs) != len(
            state["simulation_results"]
        ):
            raise ValueError("State result references do not match the validated evidence")
        for identity, expected_ref in expected_refs.items():
            if any(actual_refs[identity].get(k) != v for k, v in expected_ref.items()):
                raise ValueError("State result summary/reference disagrees with evidence")
            actual = actual_refs[identity]
            if (
                actual.get("scenario_id", state["scenario_id"]) != state["scenario_id"]
                or actual.get("schema_version", "1") != "1"
            ):
                raise ValueError("State result reference has incompatible scene metadata")
            expected_tool = next(
                call["name"] for call in reversed(calls) if identity in call["result_ids"]
            )
            if actual.get("tool_name", expected_tool) != expected_tool:
                raise ValueError("State result reference has incompatible tool metadata")
        actual_comparison = state["comparison_result"]
        if expected_comparison is not None:
            if actual_comparison is None or any(
                actual_comparison.get(k) != v for k, v in expected_comparison.items()
            ):
                raise ValueError("State comparison summary/reference disagrees with evidence")
            if (
                actual_comparison.get("scenario_id", state["scenario_id"]) != state["scenario_id"]
                or actual_comparison.get("schema_version", "1") != "1"
                or actual_comparison.get("tool_name", expected_comparison_tool)
                != expected_comparison_tool
            ):
                raise ValueError("State comparison has incompatible scene/tool metadata")
        elif actual_comparison is not None:
            raise ValueError("Single runs cannot claim a comparison result")
        return evidence

    def _report(self, state: SimulationAgentState) -> JSONDict:
        if not state["validation_result"].get("sufficient"):
            return failure(state, "report", "Cannot report unvalidated evidence")
        try:
            scene = state["scenario_id"]
            if scene is None:
                raise ValueError("Validated evidence has no scene")
            calls = [
                item
                for item in state["tool_calls"]
                if item["status"] == "success" and item["round"] == state["planning_round"]
            ]
            evidence = self._verified_evidence(state, calls)
            comparisons = [
                result
                for result in evidence
                if isinstance(result, (ComparisonResult, ServerComparisonResult))
            ]
            report_evidence = [comparisons[-1]] if comparisons else evidence
            text = SCENARIOS[scene].reporter(report_evidence, dict(state))
            result = report_evidence[-1]
        except (ValueError, OSError, KeyError, IndexError) as exc:
            return failure(state, "report", f"Final evidence unavailable: {exc}")
        directory = self.store.run_dir(state["run_id"])
        completion = decision(
            state, "report", "complete", "Report generated exclusively from validated tool evidence"
        )
        artifacts: JSONDict = {
            "report": str(directory / "report.md"),
            "decisions": str(directory / "decisions.json"),
        }
        if isinstance(result, (ComparisonResult, ServerComparisonResult)):
            self.store.write(
                directory / "comparison.json", json_bytes(result.model_dump(mode="json"))
            )
            artifacts["comparison"] = str(directory / "comparison.json")
        else:
            artifacts["simulation_results"] = [item["path"] for item in state["simulation_results"]]
        self.store.write(directory / "report.md", text.encode("utf-8"))
        self.store.write(
            directory / "decisions.json", json_bytes(state["decision_history"] + completion)
        )
        return {
            "report": text,
            "status": "completed",
            "next_action": "end",
            "artifacts": artifacts,
            "decision_history": completion,
        }
