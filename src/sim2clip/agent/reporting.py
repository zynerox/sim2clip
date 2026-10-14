"""Scene reports from already independently verified, typed evidence."""

from collections.abc import Sequence

from sim2clip.agent.state import JSONDict
from sim2clip.scenario_registry import SimulationOutput
from sim2clip.scenarios.server_contracts import ServerComparisonResult, ServerSimulationResult
from sim2clip.simulation.schemas import ComparisonResult, SimulationResult


def _header(context: JSONDict) -> list[str]:
    return [
        "# Sim2Clip 实验报告",
        f"用户问题：{context['user_query']}",
        f"实验目标：{context['experiment_goal']}",
        f"规划模式：{context['planner_mode']}。所有实验数值来自 MCP Server 的 SimPy 计算。",
    ]


def queue_report(results: Sequence[SimulationOutput], context: JSONDict) -> str:
    last = results[-1]
    workload = context["design"]["workload"]
    sections = _header(context) + [
        f"模型假设：共享 FIFO 队列，顾客 {workload.get('customers')}；"
        f"指数到达间隔均值 {workload.get('mean_interarrival_time')} 秒，"
        f"指数服务时长均值 {workload.get('mean_service_time')} 秒。"
    ]
    if isinstance(last, ComparisonResult):
        rows = ["| 收银台 | 平均等待（秒） | 最大队长均值 |", "| --- | ---: | ---: |"]
        rows += [
            f"| {s.cashiers} | {s.average_wait_time.mean:.2f} | {s.max_queue_length.mean:.2f} |"
            for s in last.summaries
        ]
        sections += [
            "\n".join(rows),
            f"在这 {len(last.seeds)} 个配对种子的实验中，"
            f"平均等待最短的配置为 {list(last.winner_cashiers)} 个收银台。",
            f"种子：{list(last.seeds)}；comparison_id：{last.comparison_id}",
        ]
    else:
        rows = ["| 收银台 | 顾客 | 平均等待（秒） |", "| --- | ---: | ---: |"]
        for result in results:
            if not isinstance(result, SimulationResult):
                raise ValueError("Wrong queue report evidence")
            rows.append(
                f"| {result.config.cashiers} | {result.config.customers} | "
                f"{result.metrics.average_wait_time:.2f} |"
            )
        sections += [
            "\n".join(rows),
            "单次有限顾客实验；当前结果用于描述该种子的观察。",
            "完整配置、种子、记录与事件见工具响应 JSON。",
        ]
    sections.append(
        "已独立复核事件、人数守恒、FIFO、收银台容量及指标。"
        + (
            "配对实验共享顾客样本。最低种子数是证据预算策略，不能证明统计显著性或真实店铺表现。"
            if isinstance(last, ComparisonResult)
            else "本报告描述独立模拟，不包含配对比较，不能证明统计显著性或真实店铺表现。"
        )
    )
    return "\n\n".join(sections) + "\n"


def server_report(results: Sequence[SimulationOutput], context: JSONDict) -> str:
    last = results[-1]
    sections = _header(context)
    configs: list[str] = []
    if isinstance(last, ServerComparisonResult):
        groups = [(e.group_id, e.config) for e in last.request.experiments]
        sections.append(
            f"比较轴：{last.request.comparison_axis}；baseline：{last.baseline_group_id}；"
            f"配对种子：{list(last.seeds)}；comparison_id：{last.comparison_id}"
        )
        rows = [
            "| 实验组 | 平均响应（秒） | 每次 P95 的均值（秒） | 平均等待（秒） | "
            "队列峰值均值 | 总完成时间均值（秒） |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        rows += [
            f"| {s.group_id} | {s.average_response_time.mean:.6f} | "
            f"{s.p95_response_time.mean:.6f} | {s.average_wait_time.mean:.6f} | "
            f"{s.max_queue_length.mean:.2f} | {s.simulation_end_time.mean:.6f} |"
            for s in last.summaries
        ]
        conclusion = (
            f"平均响应时间数值最低的组：{list(last.winner_group_ids)}。"
            "差值方向为 candidate − baseline。"
        )
    elif isinstance(last, ServerSimulationResult):
        groups = [("single", last.config)]
        m = last.metrics
        rows = [
            "| 平均响应（秒） | P95（秒） | 平均等待（秒） | 队列峰值 | "
            "完成请求 | 总完成时间（秒） |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| {m.average_response_time:.6f} | {m.p95_response_time:.6f} | "
            f"{m.average_wait_time:.6f} | "
            f"{m.max_queue_length} | {m.completed_requests} | {m.simulation_end_time:.6f} |",
        ]
        conclusion = f"单次有限请求实验，seed={last.config.seed}；result_id：{last.result_id}。"
        if len(results) > 1:
            # Repeated singles are not a fabricated paired comparison.
            rows = [
                "| Seed | 平均响应（秒） | P95（秒） | 平均等待（秒） | 队列峰值 | 完成请求 |",
                "| ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for run in results:
                if not isinstance(run, ServerSimulationResult):
                    raise ValueError("Wrong server report evidence")
                m = run.metrics
                rows.append(
                    f"| {run.config.seed} | {m.average_response_time:.6f} | "
                    f"{m.p95_response_time:.6f} | "
                    f"{m.average_wait_time:.6f} | {m.max_queue_length} | {m.completed_requests} |"
                )
    else:
        raise ValueError("Wrong server report evidence")
    for group, config in groups:
        configs.append(
            f"{group}：请求 {config.requests}；策略 {config.routing_strategy}；"
            f"服务器 (ID, work/秒, 槽数)="
            f"{[(s.server_id, s.processing_rate, s.capacity) for s in config.servers]}；"
            f"到达假设 {config.arrivals.model_dump(mode='json')}；"
            f"工作量假设 {config.workload.model_dump(mode='json')}。"
        )
    sections += [
        "\n\n".join(configs),
        "\n".join(rows),
        conclusion,
        "P95 使用 nearest-rank；"
        + (
            "多种子表是每次实验 P95 的均值，不是所有请求合并后的 P95。"
            "配对组共享到达时间和工作量，每节点独立 FIFO、固定单槽；LO 按在途请求数选择，"
            "不看速率或剩余工作量，不保证优于 Round Robin。"
            if isinstance(last, ServerComparisonResult)
            else "本报告描述独立模拟，不包含配对比较或组间胜负结论。"
        ),
        "模型限制：有限请求全部排空，服务器速率固定、节点在线，分流/传输零耗时、队列无业务容量上限。"
        "不含真实 CPU 争用、网络延迟、故障、超时、重试或扩缩容。"
        "这些是模型假设下的描述性模拟结果，不是生产服务器性能预测或统计显著性结论。",
    ]
    return "\n\n".join(sections) + "\n"
