import copy

import pytest
from pydantic import TypeAdapter, ValidationError

from sim2clip.scenarios.server_contracts import (
    RequestRecord,
    RequestSample,
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerMetricSummary,
    ServerSimulationConfig,
    ServerSimulationEvent,
    ServerSimulationResult,
)
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation


def comparison_payload():
    return {
        "comparison_axis": "routing_strategy",
        "seeds": [42],
        "experiments": [
            {"group_id": "rr", "config": {"requests": 3}},
            {"group_id": "lo", "config": {"requests": 3, "routing_strategy": "least_outstanding"}},
        ],
    }


@pytest.mark.parametrize(
    "update",
    [
        {"requests": True},
        {"requests": "3"},
        {"requests": 1.0},
        {"requests": 0},
        {"requests": 10001},
        {"seed": -1},
        {"seed": True},
        {"seed": 2**32},
        {"unknown": 0},
        {"scenario": "convenience_store_rush"},
        {"routing_strategy": "weighted"},
        {"servers": []},
        {"servers": [{"server_id": 1}] * 33},
        {"servers": [{"server_id": 2}, {"server_id": 1}]},
        {"servers": [{"server_id": 1}, {"server_id": 1}]},
        *({"servers": [{"server_id": value}]} for value in (0, True, "1", 2**31)),
        *({"servers": [{"server_id": 1, "capacity": value}]} for value in (0, 2, True, "1")),
        *(
            {"servers": [{"server_id": 1, "processing_rate": value}]}
            for value in (0, 0.0009, 1000001, True, "1", float("nan"), float("inf"), -float("inf"))
        ),
        {"arrivals": {"mode": "batch", "mean_interarrival_time": 1}},
        {"workload": {"mode": "constant", "mean_work": 1}},
        {"arrivals": {"mode": "normal"}},
        {"workload": {"mode": "pareto"}},
        {"arrivals": {"mode": "batch", "arrival_time": -1}},
        {"arrivals": {"mode": "batch", "arrival_time": 1e9 + 1}},
        {"arrivals": {"mode": "exponential", "mean_interarrival_time": 0.0009}},
        {"arrivals": {"mode": "exponential", "mean_interarrival_time": 10001}},
        {"workload": {"mode": "exponential", "mean_work": float("nan")}},
        {"workload": {"mode": "constant", "work": 0}},
        {"workload": {"mode": "constant", "work": 1e6 + 1}},
    ],
)
def test_strict_config_rejects_invalid_parameters(update):
    with pytest.raises(ValidationError):
        ServerSimulationConfig.model_validate(update)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", True),
        ("request_id", -1),
        ("request_id", 2**31),
        ("arrival_time", -1),
        ("arrival_time", float("inf")),
        ("work", "1"),
        ("work", True),
        ("work", 0),
        ("work", float("nan")),
    ],
)
def test_strict_samples(field, value):
    with pytest.raises(ValidationError):
        RequestSample.model_validate({"request_id": 0, "arrival_time": 0, "work": 1, field: value})


def test_defaults_boundaries_and_immutable_models():
    config = ServerSimulationConfig()
    assert (config.requests, config.seed, config.servers[0].processing_rate) == (1000, 42, 1.0)
    assert config.arrivals.mean_interarrival_time == 0.01
    assert config.workload.mean_work == 0.025
    with pytest.raises(ValidationError, match="frozen"):
        config.seed = 4
    ServerSimulationConfig(
        requests=10000,
        seed=2**32 - 1,
        servers=(
            {"server_id": 1, "processing_rate": 0.001},
            {"server_id": 2**31 - 1, "processing_rate": 1e6},
        ),
    )
    RequestSample(request_id=2**31 - 1, arrival_time=1e9, work=1e6)
    ServerSimulationConfig.model_validate(
        {
            "arrivals": {"mode": "batch", "arrival_time": 1e9},
            "workload": {"mode": "constant", "work": 1e6},
        }
    )


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_groups",
        "duplicate_config",
        "unsorted_seed",
        "duplicate_seed",
        "seed_conflict",
        "different_load",
        "different_pool",
        "different_strategy",
        "too_many_seeds",
        "group_format",
        "mixed_scene",
        "excess_budget",
    ],
)
def test_comparison_design_constraints(kind):
    data = comparison_payload()
    groups = data["experiments"]
    if kind == "duplicate_groups":
        groups[1]["group_id"] = "rr"
    elif kind == "duplicate_config":
        groups[1]["config"] = dict(groups[0]["config"])
    elif kind == "unsorted_seed":
        data["seeds"] = [43, 42]
    elif kind == "duplicate_seed":
        data["seeds"] = [42, 42]
    elif kind == "seed_conflict":
        groups[1]["config"]["seed"] = 43
    elif kind == "different_load":
        groups[1]["config"]["requests"] = 4
    elif kind == "different_pool":
        groups[1]["config"]["servers"] = [{"server_id": 2}]
    elif kind == "different_strategy":
        data["comparison_axis"] = "server_pool"
    elif kind == "too_many_seeds":
        data["seeds"] = list(range(42, 53))
    elif kind == "group_format":
        groups[1]["group_id"] = "9 Server"
    elif kind == "mixed_scene":
        groups[1]["config"]["scenario"] = "convenience_store_rush"
    else:
        data["seeds"] = list(range(42, 48))
        for group in groups:
            group["config"]["requests"] = 10000
    with pytest.raises(ValidationError):
        ServerComparisonRequest.model_validate(data)


def test_request_run_budget_accepts_exact_limit_and_rejects_one_extra_seed():
    data = comparison_payload()
    for group in data["experiments"]:
        group["config"]["requests"] = 10000
    data["seeds"] = list(range(42, 47))
    assert ServerComparisonRequest.model_validate(data).experiments[0].config.requests == 10000
    data["seeds"].append(47)
    with pytest.raises(ValidationError, match="budget"):
        ServerComparisonRequest.model_validate(data)


def test_all_contracts_round_trip_expose_precise_schema_and_reject_unknown_fields():
    request = ServerComparisonRequest.model_validate(comparison_payload())
    comparison = run_server_comparison(request)
    run = comparison.results[0].result
    objects = (
        request,
        request.experiments[0].config,
        run.request_samples[0],
        run.request_records[0],
        run.metrics,
        run,
        comparison,
        *run.events,
    )
    for obj in objects:
        cls = type(obj)
        assert cls.model_validate_json(obj.model_dump_json()) == obj
        assert cls.model_validate(obj.model_dump(mode="json")) == obj
        assert cls.model_json_schema()["additionalProperties"] is False
        data = obj.model_dump(mode="json")
        data["unexpected"] = 0
        with pytest.raises(ValidationError):
            cls.model_validate(data)
    adapter = TypeAdapter(ServerSimulationEvent)
    assert len(adapter.json_schema()["oneOf"]) == 4
    for event in run.events:
        assert adapter.validate_json(adapter.dump_json(event)) == event
    arrival = run.events[0].model_dump(mode="json")
    for key in ("server_id", "server_queue_length_after_event", "server_active_after_event"):
        bad = dict(arrival)
        bad.pop(key)
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


@pytest.mark.parametrize(
    "kind",
    [
        "count",
        "ids",
        "order",
        "sequence",
        "server",
        "sample_work",
        "completed",
        "per_server",
        "event_count",
        "lifecycle",
    ],
)
def test_result_structural_constraints(kind):
    result = run_server_simulation(ServerSimulationConfig(requests=3))
    data = result.model_dump(mode="json")
    if kind == "count":
        data["request_samples"].pop()
    elif kind == "ids":
        data["request_samples"][1]["request_id"] = 0
    elif kind == "order":
        data["request_records"].reverse()
    elif kind == "sequence":
        data["events"][0]["sequence"] = 1
    elif kind == "server":
        data["request_records"][0]["server_id"] = 2
    elif kind == "sample_work":
        data["request_records"][0]["work"] += 1
    elif kind == "completed":
        data["metrics"]["completed_requests"] = 2
    elif kind == "per_server":
        data["metrics"]["per_server"][0]["server_id"] = 2
    elif kind == "event_count":
        data["events"].pop()
    else:
        data["events"][-1]["request_id"] = 0
    with pytest.raises(ValidationError):
        ServerSimulationResult.model_validate(data)


@pytest.mark.parametrize(
    "kind",
    [
        "run_order",
        "missing_run",
        "seed",
        "baseline",
        "summary_ref",
        "summary_order",
        "ranking",
        "winner",
        "difference_ref",
    ],
)
def test_comparison_result_structure(kind):
    result = run_server_comparison(ServerComparisonRequest.model_validate(comparison_payload()))
    data = copy.deepcopy(result.model_dump(mode="json"))
    if kind == "run_order":
        data["results"].reverse()
    elif kind == "missing_run":
        data["results"].pop()
    elif kind == "seed":
        data["seeds"] = [43]
    elif kind == "baseline":
        data["baseline_group_id"] = "lo"
    elif kind == "summary_ref":
        data["summaries"][0]["result_ids"][0] = "a" * 64
    elif kind == "summary_order":
        data["summaries"].reverse()
    elif kind == "ranking":
        data["ranking_group_ids"] = ["rr", "rr"]
    elif kind == "winner":
        data["winner_group_ids"] = ["rr", "rr"]
    else:
        data["paired_differences"][0]["candidate_result_id"] = "a" * 64
    with pytest.raises(ValidationError):
        ServerComparisonResult.model_validate(data)


def test_metric_summary_rejects_inverted_range():
    with pytest.raises(ValidationError):
        ServerMetricSummary(mean=2, minimum=0, maximum=1)


@pytest.mark.parametrize(
    "update",
    [
        {"dispatch_time": 1.1},
        {"processing_start_time": 0.5},
        {"processing_end_time": 2},
        {"processing_time": 4},
        {"wait_time": 4},
        {"response_time": 4},
        {"processing_time": 0},
        {"processing_end_time": float("inf")},
        {"response_time": True},
    ],
)
def test_record_local_time_relationships(update):
    data = {
        "request_id": 0,
        "server_id": 1,
        "arrival_time": 1,
        "work": 1,
        "dispatch_time": 1,
        "processing_start_time": 2,
        "processing_end_time": 3,
        "processing_time": 1,
        "wait_time": 1,
        "response_time": 2,
    }
    with pytest.raises(ValidationError):
        RequestRecord.model_validate({**data, **update})


@pytest.mark.parametrize(
    "kind,update",
    [
        ("request_arrived", {"server_id": 1}),
        ("request_arrived", {"server_active_after_event": 0}),
        ("request_started", {"server_active_after_event": 0}),
        ("request_completed", {"server_active_after_event": 1}),
        ("request_routed", {"server_active_after_event": True}),
        ("request_routed", {"server_active_after_event": 2}),
        ("request_routed", {"server_id": None}),
        ("request_routed", {"server_queue_length_after_event": -1}),
        ("request_routed", {"total_queue_length_after_event": True}),
        ("request_routed", {"sequence": 40000}),
        ("request_routed", {"timestamp": float("nan")}),
    ],
)
def test_event_discriminator_and_strict_counts(kind, update):
    data = {
        "event_type": kind,
        "sequence": 0,
        "timestamp": 0,
        "request_id": 0,
        "server_id": 1,
        "server_queue_length_after_event": 0,
        "server_active_after_event": int(kind == "request_started"),
        "total_queue_length_after_event": 0,
    }
    if kind == "request_arrived":
        for field in ("server_id", "server_queue_length_after_event", "server_active_after_event"):
            data[field] = None
    with pytest.raises(ValidationError):
        TypeAdapter(ServerSimulationEvent).validate_python({**data, **update})
