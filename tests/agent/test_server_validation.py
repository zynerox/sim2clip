"""Shape-valid evidence corruption must fail independent semantic verification."""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from sim2clip.agent.server_validation import validate_server_comparison, validate_server_simulation
from sim2clip.scenarios.server_contracts import (
    RequestSample,
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerSimulationConfig,
    ServerSimulationResult,
)
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation


def supplied_result(strategy="round_robin"):
    return run_server_simulation(
        ServerSimulationConfig(
            requests=3, routing_strategy=strategy, servers=({"server_id": 1}, {"server_id": 2})
        ),
        tuple(
            RequestSample(request_id=i, arrival_time=a, work=w)
            for i, a, w in ((0, 0, 10), (1, 1, 1), (2, 3, 1))
        ),
    )


def resign(data):
    def digest(value):
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode()
        ).hexdigest()

    data["workload_hash"] = digest(
        {"workload_version": "server-workload-1", "samples": data["request_samples"]}
    )
    data["result_id"] = digest(
        {
            "contract": "server-lb-result-v1",
            "config": data["config"],
            "workload_hash": data["workload_hash"],
            "workload_source": data["workload_source"],
            "metadata": data["metadata"],
        }
    )


@pytest.mark.parametrize("strategy", ["round_robin", "least_outstanding"])
def test_valid_generated_and_hand_fixtures(strategy):
    hand = supplied_result(strategy)
    validate_server_simulation(hand, hand.config)
    result = run_server_simulation(
        ServerSimulationConfig(
            requests=100,
            routing_strategy=strategy,
            servers=({"server_id": 2}, {"server_id": 7, "processing_rate": 0.5}),
        )
    )
    validate_server_simulation(result, result.config)


@pytest.mark.parametrize(
    "kind",
    [
        "mean_response",
        "p95",
        "mean_wait",
        "end_time",
        "peak",
        "node_peak",
        "node_completed",
        "total_counter",
        "node_counter",
        "active_counter",
        "wrong_routing",
        "strategy",
        "sample_hash",
        "result_id",
        "work_rate",
        "wait",
        "response",
    ],
)
def test_shape_valid_single_result_tampering_is_rejected(kind):
    data = supplied_result().model_dump(mode="json")
    metric = data["metrics"]
    if kind == "mean_response":
        metric["average_response_time"] += 1
    elif kind == "p95":
        metric["p95_response_time"] += 1
    elif kind == "mean_wait":
        metric["average_wait_time"] += 1
    elif kind == "end_time":
        metric["simulation_end_time"] += 1
    elif kind == "peak":
        metric["max_queue_length"] = 2
    elif kind == "node_peak":
        metric["per_server"][1]["max_queue_length"] = 1
    elif kind == "node_completed":
        metric["per_server"][0]["completed_requests"] -= 1
        metric["per_server"][1]["completed_requests"] += 1
    elif kind == "total_counter":
        data["events"][0]["total_queue_length_after_event"] = 1
    elif kind == "node_counter":
        data["events"][1]["server_queue_length_after_event"] = 2
    elif kind == "active_counter":
        data["events"][1]["server_active_after_event"] = 1
    elif kind == "wrong_routing":
        data["events"][1]["server_id"] = 2
    elif kind == "strategy":
        data["config"]["routing_strategy"] = "least_outstanding"
        resign(data)
    elif kind == "sample_hash":
        data["workload_hash"] = "a" * 64
    elif kind == "result_id":
        data["result_id"] = "a" * 64
    elif kind == "work_rate":
        data["request_samples"][0]["work"] = 20
        data["request_records"][0]["work"] = 20
        resign(data)
    else:
        # A local tolerance-sized lie passes record shape; event identity is exact.
        record = data["request_records"][2]
        record["processing_start_time"] += 1e-10
        record["processing_end_time"] += 1e-10
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError):
        validate_server_simulation(parsed)


def test_generated_sample_lie_with_fresh_hashes_is_rejected():
    data = run_server_simulation(ServerSimulationConfig(requests=3)).model_dump(mode="json")
    data["config"]["seed"] = 43
    resign(data)
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError, match="Generated samples"):
        validate_server_simulation(parsed)


def test_same_time_illegal_event_order_is_rejected_despite_valid_structure():
    result = run_server_simulation(
        ServerSimulationConfig(requests=3),
        tuple(
            RequestSample(request_id=i, arrival_time=a, work=1)
            for i, a in ((0, 0), (1, 0.5), (2, 1))
        ),
    )
    data = result.model_dump(mode="json")
    # Complete then ARRIVE+ROUTE then old waiter: monotonic time and all event types intact.
    at_one = [e for e in data["events"] if e["timestamp"] == 1]
    bad_order = [at_one[0], at_one[2], at_one[3], at_one[1]]
    begin = data["events"].index(at_one[0])
    data["events"][begin : begin + 4] = bad_order
    for i, event in enumerate(data["events"]):
        event["sequence"] = i
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError, match="order"):
        validate_server_simulation(parsed)


def comparison_result():
    design = ServerComparisonRequest.model_validate(
        {
            "comparison_axis": "routing_strategy",
            "seeds": [42, 43],
            "experiments": [
                {
                    "group_id": "rr",
                    "config": {"requests": 3, "servers": [{"server_id": 1}, {"server_id": 2}]},
                },
                {
                    "group_id": "lo",
                    "config": {
                        "requests": 3,
                        "servers": [{"server_id": 1}, {"server_id": 2}],
                        "routing_strategy": "least_outstanding",
                    },
                },
            ],
        }
    )
    loads = {seed: supplied_result().request_samples for seed in design.seeds}
    return run_server_comparison(design, loads)


@pytest.mark.parametrize(
    "kind",
    [
        "summary",
        "p95_summary",
        "difference",
        "queue_difference",
        "ranking",
        "winner",
        "comparison_id",
        "source_pair",
    ],
)
def test_shape_valid_comparison_corruption_is_rejected(kind):
    original = comparison_result()
    validate_server_comparison(original, original.request)
    data = original.model_dump(mode="json")
    if kind in ("summary", "p95_summary"):
        metric = "average_response_time" if kind == "summary" else "p95_response_time"
        for key in ("mean", "minimum", "maximum"):
            data["summaries"][0][metric][key] += 1
    elif kind == "difference":
        data["paired_differences"][0]["average_response_time_difference"] += 1
    elif kind == "queue_difference":
        data["paired_differences"][0]["max_queue_length_difference"] = 0
    elif kind == "ranking":
        data["ranking_group_ids"].reverse()
    elif kind == "winner":
        data["winner_group_ids"] = ["rr"]
    elif kind == "comparison_id":
        data["comparison_id"] = "a" * 64
    else:
        run = data["results"][0]["result"]
        run["workload_source"] = "generated"
        resign(run)
        data["summaries"][0]["result_ids"][0] = run["result_id"]
        data["paired_differences"][0]["baseline_result_id"] = run["result_id"]
    parsed = ServerComparisonResult.model_validate(data)
    with pytest.raises(ValueError):
        validate_server_comparison(parsed)


def test_expected_input_locks_and_unvalidated_model_copies_are_rechecked():
    run = supplied_result()
    with pytest.raises(ValueError, match="config"):
        validate_server_simulation(run, run.config.model_copy(update={"seed": 43}))
    with pytest.raises(ValueError):
        validate_server_simulation(run.model_copy(update={"events": ()}))
    result = comparison_result()
    other = result.request.model_copy(update={"seeds": (42,)})
    with pytest.raises(ValueError):
        validate_server_comparison(result, other)


def test_independent_validator_imports_only_contracts_and_standard_library():
    import sys

    path = Path(__file__).parents[2] / "src/sim2clip/agent/server_validation.py"
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        assert all(
            m == "sim2clip.scenarios.server_contracts" or m.split(".")[0] in sys.stdlib_module_names
            for m in modules
        )


def test_coherently_swapped_fifo_waiters_are_rejected():
    result = run_server_simulation(
        ServerSimulationConfig(requests=3),
        tuple(
            RequestSample(request_id=i, arrival_time=0, work=w) for i, w in ((0, 3), (1, 1), (2, 1))
        ),
    )
    data = result.model_dump(mode="json")
    # Swap complete lifecycles after routing, including records, so shape stays valid.
    for event in data["events"]:
        if event["event_type"] in ("request_started", "request_completed") and event["request_id"]:
            event["request_id"] = 3 - event["request_id"]
    for rid, start, end in ((1, 4, 5), (2, 3, 4)):
        data["request_records"][rid].update(
            processing_start_time=start, processing_end_time=end, wait_time=start, response_time=end
        )
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError, match="FIFO"):
        validate_server_simulation(parsed)


def test_overlapping_service_with_consistent_record_times_is_rejected():
    result = run_server_simulation(
        ServerSimulationConfig(requests=2),
        (
            RequestSample(request_id=0, arrival_time=0, work=3),
            RequestSample(request_id=1, arrival_time=1, work=2),
        ),
    )
    data = result.model_dump(mode="json")
    data["request_records"][1].update(
        processing_start_time=1, processing_end_time=3, wait_time=0, response_time=2
    )
    for event in data["events"]:
        if event["request_id"] == 1:
            if event["event_type"] == "request_started":
                event["timestamp"] = 1
            if event["event_type"] == "request_completed":
                event["timestamp"] = 3
    data["events"].sort(key=lambda e: e["timestamp"])
    for i, event in enumerate(data["events"]):
        event["sequence"] = i
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError):
        validate_server_simulation(parsed)


def test_lo_tie_cannot_be_randomized_or_rate_weighted():
    data = supplied_result("least_outstanding").model_dump(mode="json")
    # First request is moved to node 2 in records and every bound event; all counters
    # remain valid in shape. Equal outstanding counts still require smallest ID 1.
    data["request_records"][0]["server_id"] = 2
    for event in data["events"]:
        if event["request_id"] == 0 and event["server_id"] is not None:
            event["server_id"] = 2
    parsed = ServerSimulationResult.model_validate(data)
    with pytest.raises(ValueError, match="routing"):
        validate_server_simulation(parsed)


def test_pairing_checks_reject_two_individually_valid_different_workloads():
    original = comparison_result()
    replacement = run_server_simulation(
        original.results[1].result.config,
        tuple(
            sample.model_copy(update={"work": sample.work * 2})
            for sample in original.results[1].result.request_samples
        ),
    )
    validate_server_simulation(replacement)
    data = original.model_dump(mode="json")
    data["results"][1]["result"] = replacement.model_dump(mode="json")
    data["summaries"][1]["result_ids"][0] = replacement.result_id
    data["paired_differences"][0]["candidate_result_id"] = replacement.result_id
    parsed = ServerComparisonResult.model_validate(data)
    with pytest.raises(ValueError, match="share samples"):
        validate_server_comparison(parsed)
