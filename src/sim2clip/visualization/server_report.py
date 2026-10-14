"""Fallback report for saved server evidence without an Agent report."""

from sim2clip.scenarios.server_contracts import ServerComparisonResult
from sim2clip.visualization.data import ServerResult


def saved_server_report(result: ServerResult) -> str:
    lines = ["# Sim2Clip 服务器请求分流实验", "从已验证 JSON 回放，不重新执行 MCP 或模拟。"]
    if isinstance(result, ServerComparisonResult):
        configs = [(e.group_id, e.config) for e in result.request.experiments]
        sources = sorted({r.result.workload_source for r in result.results})
        lines += [
            f"比较轴：{result.request.comparison_axis}；配对 Seed：{list(result.seeds)}。",
            "下表为种子间均值 [最小, 最大]；每次 P95 的均值不是合并请求后的 P95。",
            "| 组别 | 平均响应/s | 每次 P95 均值/s | 平均等待/s | 队列峰值均值 | 完成时间均值/s |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for summary in result.summaries:
            values = (
                summary.average_response_time,
                summary.p95_response_time,
                summary.average_wait_time,
                summary.max_queue_length,
                summary.simulation_end_time,
            )
            cells = [f"{v.mean:.6f} [{v.minimum:.6f}, {v.maximum:.6f}]" for v in values]
            lines.append(f"| {summary.group_id} | " + " | ".join(cells) + " |")
        lines += [
            "候选减 baseline 的逐 Seed 响应差："
            + str(
                [
                    (d.seed, d.candidate_group_id, d.average_response_time_difference)
                    for d in result.paired_differences
                ]
            ),
            f"平均响应数值最低组：{list(result.winner_group_ids)}；不代表统计显著性。",
        ]
    else:
        configs = [("single", result.config)]
        sources = [result.workload_source]
        m = result.metrics
        lines += [
            f"单次 Seed：{result.config.seed}；完成 {m.completed_requests} 请求。",
            f"平均响应 {m.average_response_time:.6f}s；P95 {m.p95_response_time:.6f}s；"
            f"平均等待 {m.average_wait_time:.6f}s；队列峰值 {m.max_queue_length}；"
            f"全部完成时间 {m.simulation_end_time:.6f}s。",
        ]
    for group, c in configs:
        lines.append(
            f"{group}：{c.requests} 请求；{c.routing_strategy}；"
            f"服务器 (ID, work/s)={[(s.server_id, s.processing_rate) for s in c.servers]}；"
            f"到达 {c.arrivals.model_dump(mode='json')}；"
            f"工作量 {c.workload.model_dump(mode='json')}。"
        )
    lines += [
        f"工作负载来源：{sources}。supplied 使用实际 request_samples，"
        "不声称手工样本符合配置中的采样分布；generated 按声明 Seed/独立随机流复现。",
        "每服务器独立 FIFO、单槽、固定速率；处理时间=工作量/速率。"
        "RR 轮换；LO 按等待+处理中计数，平局选最小 ID，不看速率，不保证获胜。",
        "有限请求排空；P95 nearest-rank；"
        "共享配对样本；无网络延迟、故障、重试、超时或扩缩容。"
        "网络路径只是示意，不代表真实拓扑或生产服务器性能预测。",
        "服务器场景当前不支持 GIF 导出；HTML 可离线回放。",
    ]
    return "\n\n".join(lines) + "\n"
