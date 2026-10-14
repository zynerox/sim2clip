from collections.abc import Iterator
from pathlib import Path

import pytest

from sim2clip.simulation import CustomerSample


@pytest.fixture
def fixed_samples() -> tuple[CustomerSample, ...]:
    """Three simultaneous arrivals, with hand-calculable service times."""
    return tuple(
        CustomerSample(customer_id=index, arrival_time=0.0, service_time=duration)
        for index, duration in enumerate((3.0, 2.0, 1.0))
    )


@pytest.fixture(autouse=True)
def protect_global_random_state(request: pytest.FixtureRequest) -> Iterator[None]:
    import random

    # This invariant applies to the engine/MCP, not orchestration libraries.
    # LangGraph itself draws random bits when allocating checkpoint IDs.
    if any(
        request.node.path.is_relative_to(Path(__file__).parent / folder)
        for folder in ("agent", "cli")
    ):
        yield
        return
    before = random.getstate()
    yield
    assert random.getstate() == before, "Simulation must not mutate the global random generator"
