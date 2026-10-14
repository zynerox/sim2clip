from unittest.mock import Mock

import pytest

from sim2clip.agent.server_validation import validate_server_comparison
from sim2clip.scenarios.server_contracts import RequestSample, ServerComparisonRequest
from sim2clip.simulation import server_comparison as comparison


def request(axis="routing_strategy", seeds=(42, 43), requests=20):
    first = {"requests": requests, "servers": [{"server_id": 1}, {"server_id": 2}]}
    second = {**first, "routing_strategy": "least_outstanding"}
    if axis == "server_pool":
        first["servers"] = [{"server_id": 1}]
        second = {**first, "servers": [{"server_id": i} for i in (1, 2, 3)]}
    return ServerComparisonRequest.model_validate(
        {
            "comparison_axis": axis,
            "seeds": list(seeds),
            "experiments": [
                {"group_id": "baseline", "config": first},
                {"group_id": "candidate", "config": second},
            ],
        }
    )


@pytest.mark.parametrize("axis", ["routing_strategy", "server_pool"])
def test_paired_seeds_complete_coverage_samples_sources_and_repeatability(axis):
    design = request(axis)
    result = comparison.run_server_comparison(design)
    validate_server_comparison(result, design)
    assert result == comparison.run_server_comparison(design)
    assert [(r.result.config.seed, r.group_id) for r in result.results] == [
        (42, "baseline"),
        (42, "candidate"),
        (43, "baseline"),
        (43, "candidate"),
    ]
    for i in (0, 2):
        a, b = (r.result for r in result.results[i : i + 2])
        assert a.request_samples == b.request_samples
        assert a.workload_hash == b.workload_hash
        assert a.workload_source == b.workload_source == "generated"
        assert a.config.seed == b.config.seed
    assert result.results[0].result.request_samples != result.results[2].result.request_samples


def test_generator_called_once_per_seed_and_same_tuple_passed_to_each_group(monkeypatch):
    generate = Mock(wraps=comparison.generate_server_workload)
    simulate = Mock(wraps=comparison._simulate)
    monkeypatch.setattr(comparison, "generate_server_workload", generate)
    monkeypatch.setattr(comparison, "_simulate", simulate)
    comparison.run_server_comparison(request())
    assert generate.call_count == 2
    assert simulate.call_count == 4
    assert simulate.call_args_list[0].args[1] is simulate.call_args_list[1].args[1]
    assert simulate.call_args_list[2].args[1] is simulate.call_args_list[3].args[1]


def test_m1_supplied_strategy_comparison_exact_differences():
    demand = tuple(
        RequestSample(request_id=i, arrival_time=a, work=w)
        for i, a, w in ((0, 0, 10), (1, 1, 1), (2, 3, 1))
    )
    result = comparison.run_server_comparison(request(seeds=(42,), requests=3), {42: demand})
    validate_server_comparison(result)
    delta = result.paired_differences[0]
    assert delta.average_response_time_difference == pytest.approx(-7 / 3)
    assert delta.p95_response_time_difference == 0
    assert delta.average_wait_time_difference == pytest.approx(-7 / 3)
    assert delta.max_queue_length_difference == -1
    assert delta.simulation_end_time_difference == -1
    assert result.ranking_group_ids == ("candidate", "baseline")
    assert result.winner_group_ids == ("candidate",)
    assert all(r.result.workload_source == "supplied" for r in result.results)


def test_stable_tie_preserves_request_order_and_all_winners():
    data = request().model_dump(mode="json")
    data["experiments"][0]["group_id"] = "z_first"
    data["experiments"][1]["group_id"] = "a_second"
    for experiment in data["experiments"]:
        experiment["config"]["arrivals"] = {"mode": "batch", "arrival_time": 0}
        experiment["config"]["workload"] = {"mode": "constant", "work": 1}
    result = comparison.run_server_comparison(ServerComparisonRequest.model_validate(data))
    validate_server_comparison(result)
    assert result.ranking_group_ids == result.winner_group_ids == ("z_first", "a_second")


def test_lo_can_lose_and_no_strategy_is_assumed_to_win():
    # LO's smallest-ID tie repeatedly selects slow server 1 after each drain.
    data = request(seeds=(42,), requests=2).model_dump(mode="json")
    for group in data["experiments"]:
        group["config"]["servers"] = [
            {"server_id": 1, "processing_rate": 1},
            {"server_id": 2, "processing_rate": 10},
        ]
    demand = (
        RequestSample(request_id=0, arrival_time=0, work=1),
        RequestSample(request_id=1, arrival_time=2, work=1),
    )
    result = comparison.run_server_comparison(
        ServerComparisonRequest.model_validate(data), {42: demand}
    )
    validate_server_comparison(result)
    assert result.winner_group_ids == ("baseline",)
    assert result.paired_differences[0].average_response_time_difference > 0


def test_heterogeneous_pool_change_with_same_count_is_a_valid_axis():
    data = request("server_pool", seeds=(42,)).model_dump(mode="json")
    data["experiments"][0]["config"]["servers"] = [{"server_id": 1}, {"server_id": 2}]
    data["experiments"][1]["config"]["servers"] = [
        {"server_id": 1},
        {"server_id": 2, "processing_rate": 2},
    ]
    result = comparison.run_server_comparison(ServerComparisonRequest.model_validate(data))
    validate_server_comparison(result)


def test_seed_p95_mean_is_distinct_from_pooled_request_p95():
    design = request(seeds=(42, 43), requests=1)
    loads = {
        seed: (RequestSample(request_id=0, arrival_time=0, work=work),)
        for seed, work in ((42, 1), (43, 9))
    }
    result = comparison.run_server_comparison(design, loads)
    validate_server_comparison(result)
    assert result.summaries[0].p95_response_time.mean == 5
    assert max(r.result.metrics.p95_response_time for r in result.results) == 9


@pytest.mark.parametrize("keys", [(), (42,), (42, 43, 44), ("42", 43), (True, 43)])
def test_supplied_seed_map_requires_exact_integer_keys(keys):
    samples = (RequestSample(request_id=0, arrival_time=0, work=1),)
    with pytest.raises(ValueError, match="seeds"):
        comparison.run_server_comparison(request(requests=1), dict.fromkeys(keys, samples))
