import pytest
from pydantic import ValidationError

from sim2clip.simulation import (
    ComparisonRequest,
    ComparisonResult,
    CustomerRecord,
    CustomerSample,
    SimulationConfig,
    SimulationEvent,
    SimulationResult,
    run_comparison,
    run_simulation,
)


@pytest.mark.parametrize(
    "update",
    [
        {"customers": -100},
        {"customers": 0},
        {"customers": True},
        {"customers": "100"},
        {"customers": 10001},
        {"cashiers": 0},
        {"cashiers": 101},
        {"cashiers": 1.5},
        {"seed": -1},
        {"seed": True},
        {"seed": 2**32},
        {"mean_service_time": 0.0},
        {"mean_service_time": float("nan")},
        {"mean_interarrival_time": float("inf")},
        {"time_unit": "minute"},
        {"arrival_distribution": "normal"},
        {"typo": 1},
    ],
)
def test_invalid_config_is_rejected(update):
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate({"cashiers": 1, **update})


def test_default_config_describes_the_official_demo():
    config = SimulationConfig(cashiers=1)
    assert (config.customers, config.seed) == (100, 42)
    assert (config.mean_interarrival_time, config.mean_service_time) == (10.0, 25.0)
    assert config.scenario == "convenience_store_rush"
    assert config.time_unit == "second"


@pytest.mark.parametrize("field", ["arrival_time", "service_time"])
def test_nonfinite_samples_are_rejected(field):
    values = {"customer_id": 0, "arrival_time": 0.0, "service_time": 1.0, field: float("inf")}
    with pytest.raises(ValidationError):
        CustomerSample.model_validate(values)


def test_customer_record_rejects_inconsistent_derived_times():
    with pytest.raises(ValidationError):
        CustomerRecord(
            customer_id=0,
            cashier_id=1,
            arrival_time=0.0,
            service_time=2.0,
            service_start_time=1.0,
            service_end_time=3.0,
            wait_time=9.0,
        )


@pytest.mark.parametrize(
    ("event_type", "cashier_id"),
    [("customer_arrived", 1), ("customer_started", None), ("customer_finished", None)],
)
def test_service_events_require_cashier_and_arrivals_forbid_it(event_type, cashier_id):
    with pytest.raises(ValidationError):
        SimulationEvent(
            sequence=0,
            timestamp=0.0,
            event_type=event_type,
            customer_id=0,
            cashier_id=cashier_id,
            queue_length_after_event=0,
        )


def test_all_six_contracts_expose_json_schema_and_json_round_trip(fixed_samples):
    config = SimulationConfig(cashiers=1, customers=3)
    result = run_simulation(config, fixed_samples)
    comparison = run_comparison((config, SimulationConfig(cashiers=3, customers=3)), seeds=(42,))
    objects = (
        config,
        fixed_samples[0],
        result.customer_records[0],
        result.events[0],
        result,
        comparison,
    )
    for model in objects:
        assert type(model).model_validate_json(model.model_dump_json()) == model
        assert type(model).model_json_schema()["type"] == "object"
    assert SimulationResult.model_json_schema()["properties"]["schema_version"]["const"] == "1"
    assert ComparisonResult.model_json_schema()["properties"]["schema_version"]["const"] == "1"


def test_events_require_all_six_fields_in_json_schema():
    schema = SimulationEvent.model_json_schema()
    assert set(schema["required"]) == {
        "sequence",
        "timestamp",
        "event_type",
        "customer_id",
        "cashier_id",
        "queue_length_after_event",
    }


def test_result_rejects_broken_sequence(fixed_samples):
    result = run_simulation(SimulationConfig(cashiers=1, customers=3), fixed_samples)
    data = result.model_dump(mode="json")
    data["events"][0]["sequence"] = 2
    with pytest.raises(ValidationError):
        SimulationResult.model_validate(data)


def test_comparison_request_rejects_excessive_budget():
    configs = tuple(SimulationConfig(cashiers=i, customers=10000) for i in range(1, 9))
    with pytest.raises(ValidationError, match="budget"):
        ComparisonRequest(experiments=configs, seeds=tuple(range(10)))
