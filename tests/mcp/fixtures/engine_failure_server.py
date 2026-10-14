"""Test-only stdio server with injected failures; never imported by production."""

from collections.abc import Sequence

from sim2clip.mcp_server import simulation_server
from sim2clip.simulation import (
    ComparisonResult,
    CustomerSample,
    SimulationConfig,
    SimulationResult,
    run_comparison,
    run_simulation,
)


def injected_run(
    config: SimulationConfig, samples: Sequence[CustomerSample] | None = None
) -> SimulationResult:
    if config.customers == 1:
        raise RuntimeError("fixture-engine-failure")
    return run_simulation(config, samples)


def injected_comparison(
    experiments: Sequence[SimulationConfig], *, seeds: Sequence[int] | None = None
) -> ComparisonResult:
    if experiments[0].customers == 1:
        raise ValueError("fixture-comparison-failure")
    return run_comparison(experiments, seeds=seeds)


if __name__ == "__main__":
    simulation_server.engine_run_simulation = injected_run
    simulation_server.engine_run_comparison = injected_comparison
    simulation_server.main()
