"""Finite, persisted work budgets rather than unbounded agent loops."""

from dataclasses import asdict, dataclass

from sim2clip.agent.state import JSONDict


@dataclass(frozen=True)
class AgentPolicy:
    parameter_replans: int = 2
    tool_retries: int = 2
    result_replans: int = 2
    minimum_comparison_seeds: int = 5
    maximum_tool_calls: int = 12
    maximum_customer_runs: int = 100000
    maximum_batch_calls: int = 4

    def __post_init__(self) -> None:
        if min(self.parameter_replans, self.tool_retries, self.result_replans) < 0:
            raise ValueError("Retry budgets must be nonnegative")
        if not 1 <= self.minimum_comparison_seeds <= 10:
            raise ValueError("minimum_comparison_seeds must be between 1 and 10")
        if min(self.maximum_tool_calls, self.maximum_customer_runs, self.maximum_batch_calls) < 1:
            raise ValueError("Execution budgets must be positive")

    def to_dict(self) -> JSONDict:
        return asdict(self)
