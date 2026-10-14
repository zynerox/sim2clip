"""Engine execution only creates test fixtures; presentation consumes saved results."""

import os
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright

from sim2clip.simulation import SimulationConfig, run_comparison, run_simulation
from sim2clip.simulation.schemas import ComparisonResult, CustomerSample, SimulationResult
from sim2clip.visualization.pipeline import export_demo


@pytest.fixture(scope="session")
def official() -> ComparisonResult:
    return run_comparison(
        (SimulationConfig(cashiers=1), SimulationConfig(cashiers=3)), seeds=(42, 43, 44, 45, 46)
    )


@pytest.fixture
def hand_result() -> SimulationResult:
    return run_simulation(
        SimulationConfig(cashiers=1, customers=3),
        (
            CustomerSample(customer_id=0, arrival_time=0, service_time=4),
            CustomerSample(customer_id=1, arrival_time=1, service_time=2),
            CustomerSample(customer_id=2, arrival_time=1, service_time=1),
        ),
    )


@pytest.fixture(scope="session")
def demo_files(tmp_path_factory: Any, official: ComparisonResult) -> dict[str, str]:
    return export_demo(
        official, tmp_path_factory.mktemp("demo"), report="# Verified test report", frames=21
    )


@pytest.fixture(scope="module")
def browser() -> Any:
    with sync_playwright() as playwright:
        configured = os.getenv("SIM2CLIP_BROWSER_EXECUTABLE")
        candidates = (
            [Path(configured)]
            if configured
            else [
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            ]
        )
        executable = next((str(p) for p in candidates if p.is_file()), None)
        try:
            instance = playwright.chromium.launch(headless=True, executable_path=executable)
        except Error as exc:
            pytest.skip(f"Browser unavailable; run playwright install chromium: {exc}")
        yield instance
        instance.close()
