"""Conservative host guards for explicit scope conflicts, never tool selection.

Tool names always originate in AIMessage.tool_calls. Unrecognized phrasing is
left to the planner and declared contracts; this is not a general NL parser.
"""

import re

from sim2clip.agent.state import JSONDict
from sim2clip.scenario_registry import ScenarioId


class UnsupportedQuery(ValueError):
    pass


def check_query_scope(query: str, scenario_id: ScenarioId) -> None:
    queue = bool(
        re.search(r"便利店|收银台|顾客|\bcashiers?\b|\bcustomers?\b|\bcheckout\b", query, re.I)
    )
    server = bool(
        re.search(
            r"服务器|请求|负载均衡|分流|\bservers?\b|\brequests?\b|"
            r"round[ _-]?robin|least[ _-]?outstanding",
            query,
            re.I,
        )
    )
    if queue and server:
        raise UnsupportedQuery("一个实验只能使用一个场景；请明确便利店或服务器实验目标。")
    if (queue and scenario_id != "queue") or (server and scenario_id != "server_load_balancing"):
        raise ValueError("Selected MCP tool conflicts with the explicitly requested scenario")
    # Explicitly unsupported mechanisms cannot be satisfied by ignoring them.
    unsupported = (
        r"(?:故障|超时|重试|扩缩容|网络延迟|丢包|抢占|"
        r"\bfailures?\b|\btimeouts?\b|\bretries\b|\bretry\b|\bautoscal\w*\b|"
        r"network\s+(?:latency|delay)|packet\s+loss|\bpreempt\w*\b)"
    )
    scope = re.sub(
        rf"(?:不考虑|忽略|不包含|无需|不模拟|without|excluding)\s*{unsupported}"
        rf"(?:\s*(?:、|，|和|及|or|and)\s*{unsupported})*",
        "",
        query,
        flags=re.I,
    )
    if re.search(
        unsupported,
        scope,
        re.I,
    ):
        raise UnsupportedQuery(
            "当前仅支持有限 FIFO 容量/分流实验，不支持故障、超时、重试、扩缩容或网络机制。"
        )
    if re.search(r"库存|补货|传染病|交通信号|inventory|epidemic|traffic\s+lights?", query, re.I):
        raise UnsupportedQuery("该实验类型未注册；当前只支持便利店排队与服务器负载均衡。")


def explicit_populations(query: str, scenario_id: ScenarioId) -> list[str]:
    entities = r"顾客|customers?" if scenario_id == "queue" else r"请求|requests?"
    return re.findall(rf"(-?\d+(?:\.\d+)?)\s*(?:个|位)?\s*(?:{entities})", query, re.I)


def check_explicit_population(query: str, count: int, scenario_id: ScenarioId) -> None:
    values = explicit_populations(query, scenario_id)
    if values and any(float(value) != count for value in values):
        raise ValueError("Planned population differs from the explicit user request")


def check_explicit_comparison(query: str, design: JSONDict) -> None:
    """Reject clearly contradictory designs without constructing a tool plan."""
    if design["scenario_id"] == "server_load_balancing":
        if re.search(r"round[ _-]?robin", query, re.I) and re.search(
            r"least[ _-]?outstanding", query, re.I
        ):
            if design["comparison_axis"] != "routing_strategy":
                raise ValueError("Requested RR/LO comparison requires the routing_strategy axis")
    entity = r"收银台|cashiers?" if design["scenario_id"] == "queue" else r"服务器|servers?"
    pair = re.search(
        rf"(?:比较|compare)\s*(\d+)\s*(?:个|台)?\s*(?:{entity})?\s*"
        rf"(?:和|与|vs\.?|versus|and)\s*(\d+)\s*(?:个|台)?\s*(?:{entity})",
        query,
        re.I,
    )
    if pair:
        requested = {int(value) for value in pair.groups()}
        if design["scenario_id"] == "queue":
            actual = set(design["cashiers"])
        else:
            if design["comparison_axis"] != "server_pool":
                raise ValueError(
                    "Requested server capacity comparison requires the server_pool axis"
                )
            actual = {len(group["config"]["servers"]) for group in design["groups"]}
        if actual != requested:
            raise ValueError("Planned capacity groups differ from the explicit user request")
