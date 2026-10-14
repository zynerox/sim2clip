"""Generate the official demo's real data through the public simulation API."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from sim2clip.simulation import (
    ComparisonResult,
    CustomerRecord,
    CustomerSample,
    SimulationConfig,
    SimulationEvent,
    SimulationResult,
    run_comparison,
)


def write_model(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/demo"))
    arguments = parser.parse_args()
    output: Path = arguments.output
    comparison = run_comparison(
        (SimulationConfig(cashiers=1), SimulationConfig(cashiers=3)),
        seeds=(42, 43, 44, 45, 46),
    )
    output.mkdir(parents=True, exist_ok=True)
    write_model(output / "comparison.json", comparison)
    for result in comparison.results:
        if result.config.seed == 42:
            write_model(output / f"cashiers-{result.config.cashiers}-seed-42.json", result)
    contracts = (
        SimulationConfig,
        CustomerSample,
        CustomerRecord,
        SimulationEvent,
        SimulationResult,
        ComparisonResult,
    )
    schemas = output / "schemas"
    schemas.mkdir(exist_ok=True)
    for contract in contracts:
        (schemas / f"{contract.__name__}.schema.json").write_text(
            json.dumps(contract.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print("Convenience-store rush: 100 customers, paired seeds 42-46; all times are seconds.")
    for summary in comparison.summaries:
        print(
            f"cashiers={summary.cashiers}: mean wait={summary.average_wait_time.mean:.2f}s, "
            f"mean peak queue={summary.max_queue_length.mean:.2f}, "
            f"mean completion={summary.simulation_end_time.mean:.2f}s"
        )
    print(f"Replay JSON, full comparison and six contract schemas: {output.resolve()}")


if __name__ == "__main__":
    main()
