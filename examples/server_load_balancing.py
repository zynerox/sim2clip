"""Standalone server experiments; this does not run the Agent, MCP or product CLI.

python examples/server_load_balancing.py --experiment capacity --requests 1000 \
    --output outputs/server-m2/capacity.json
"""

import argparse
from pathlib import Path

from sim2clip.agent.server_validation import validate_server_comparison, validate_server_simulation
from sim2clip.scenarios.server_contracts import (
    BatchArrivals,
    ConstantWork,
    ExponentialArrivals,
    ExponentialWork,
    ServerComparisonRequest,
    ServerComparisonResult,
    ServerConfig,
    ServerExperiment,
    ServerSimulationConfig,
    ServerSimulationResult,
)
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment", choices=("single", "capacity", "strategy"), default="capacity"
    )
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument(
        "--seeds", help="Comma-separated ascending seeds; single:42, comparison:42..46"
    )
    parser.add_argument("--rates", default="1,1,1", help="Comma-separated work/second rates")
    parser.add_argument(
        "--strategy", choices=("round_robin", "least_outstanding"), default="round_robin"
    )
    parser.add_argument("--arrival-mode", choices=("exponential", "batch"), default="exponential")
    parser.add_argument("--mean-arrival", type=float, default=0.01)
    parser.add_argument("--batch-time", type=float, default=0.0)
    parser.add_argument("--work-mode", choices=("exponential", "constant"), default="exponential")
    parser.add_argument("--mean-work", type=float, default=0.025)
    parser.add_argument("--work", type=float, default=0.025)
    parser.add_argument(
        "--output", type=Path, help="New JSON file; existing files are never overwritten"
    )
    args = parser.parse_args()
    try:
        seeds = (
            tuple(map(int, args.seeds.split(",")))
            if args.seeds
            else ((42,) if args.experiment == "single" else (42, 43, 44, 45, 46))
        )
        rates = tuple(map(float, args.rates.split(",")))
    except ValueError as error:
        parser.error(str(error))
    if not seeds or args.experiment == "single" and len(seeds) != 1:
        parser.error("Single experiments require exactly one seed")
    if args.experiment == "capacity" and len(rates) != 3:
        parser.error("Capacity example requires exactly three rates (one vs three servers)")
    if args.experiment == "strategy" and args.strategy != "round_robin":
        parser.error("Strategy example fixes RR as baseline and LO as candidate")
    # Reject inactive command options rather than silently retaining unused assumptions.
    if args.arrival_mode == "batch" and args.mean_arrival != 0.01:
        parser.error("--mean-arrival applies only to exponential arrivals")
    if args.arrival_mode == "exponential" and args.batch_time != 0.0:
        parser.error("--batch-time applies only to batch arrivals")
    if args.work_mode == "constant" and args.mean_work != 0.025:
        parser.error("--mean-work applies only to exponential work")
    if args.work_mode == "exponential" and args.work != 0.025:
        parser.error("--work applies only to constant work")
    config = ServerSimulationConfig(
        requests=args.requests,
        seed=seeds[0],
        routing_strategy=args.strategy,
        servers=tuple(
            ServerConfig(server_id=i + 1, processing_rate=rate) for i, rate in enumerate(rates)
        ),
        arrivals=(
            BatchArrivals(arrival_time=args.batch_time)
            if args.arrival_mode == "batch"
            else ExponentialArrivals(mean_interarrival_time=args.mean_arrival)
        ),
        workload=(
            ConstantWork(work=args.work)
            if args.work_mode == "constant"
            else ExponentialWork(mean_work=args.mean_work)
        ),
    )
    result: ServerSimulationResult | ServerComparisonResult
    if args.experiment == "single":
        result = run_server_simulation(config)
        validate_server_simulation(result, config)
    else:
        if args.experiment == "capacity":
            baseline = config.model_copy(update={"servers": config.servers[:1]})
            groups = (
                ServerExperiment(group_id="one_server", config=baseline),
                ServerExperiment(group_id="three_servers", config=config),
            )
        else:
            groups = (
                ServerExperiment(group_id="rr", config=config),
                ServerExperiment(
                    group_id="lo",
                    config=config.model_copy(update={"routing_strategy": "least_outstanding"}),
                ),
            )
        request = ServerComparisonRequest(
            comparison_axis="server_pool" if args.experiment == "capacity" else "routing_strategy",
            experiments=groups,
            seeds=seeds,
        )
        result = run_server_comparison(request)
        validate_server_comparison(result, request)
    payload = result.model_dump_json(indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        print(f"Validated standalone result: {args.output.resolve()}")


if __name__ == "__main__":
    main()
