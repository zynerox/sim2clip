"""Public diagnostics from known codes and deterministic input constraints.

Never parse provider prose or choose an execution tool. Unknown no-tool replies
stay unclassified; explicit quantities are checked against the registered schema.
"""

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from pydantic import TypeAdapter, ValidationError

from sim2clip.agent.query_validation import explicit_populations
from sim2clip.agent.state import JSONDict
from sim2clip.scenario_registry import SCENARIOS, ScenarioId, tool_contract

NO_TOOL_MESSAGE = (
    "未获得可执行的工具计划，无法确认模型未调用工具的具体原因。"
    "当前仅支持便利店 FIFO 排队和服务器 RR/LO 负载均衡的有限实验；"
    "天气等未注册模型，以及故障、重试、超时、扩缩容和网络机制不受支持。"
    "请提供受支持的实验目标和合法参数；本次响应没有触发新的模拟。"
)

_MESSAGES = {
    "UNSUPPORTED_REQUEST": NO_TOOL_MESSAGE,
    "INVALID_PARAMETERS": (
        "实验参数未通过校验，未执行未通过校验的计划。请检查数量、配置和实验目标。"
    ),
    "INVALID_PLAN": "模型返回的工具计划不符合要求；请明确受支持的实验目标后重试。",
    "INVALID_PLAN_JSON": "模型返回的工具参数不是合法 JSON，未执行该计划。",
    "PLANNER_TIMEOUT": "模型规划请求超时，本次未获得可执行计划。请稍后重试。",
    "PLANNER_REQUEST_ERROR": (
        "模型请求失败。请检查服务端点、模型、密钥和可用额度；未公开供应商错误正文。"
    ),
    "MODEL_CONFIGURATION_ERROR": (
        "模型配置不可用。请检查 MODEL_API_KEY、MODEL_BASE_URL 和 MODEL_NAME。"
    ),
    "CONFIGURATION_ERROR": (
        "配置或任务状态无效。请检查 MODEL_API_KEY、MODEL_BASE_URL、MODEL_NAME 和任务 ID；"
        "已有任务请使用 resume。"
    ),
    "INVALID_EVIDENCE": "模拟结果未通过独立验证，不能生成成功结论。",
    "INSUFFICIENT_EVIDENCE": "实验结果尚不足以完成所需比较，不能生成完整结论。",
    "ENGINE_ERROR": "ENGINE_ERROR：模拟引擎执行失败，没有可用的已验证结果。",
    "TRANSPORT_ERROR": "MCP 通信失败，无法确认工具执行完成。",
    "ARTIFACT_ERROR": "结果文件缺失、损坏或与实验不匹配，不能生成成功结论。",
    "VISUALIZATION_ERROR": "结果展示导出失败；请检查已保存的结果文件和输出目录。",
    "IO_ERROR": "文件读取或写入失败，请检查文件位置、权限和可用空间。",
    "RUNTIME_ERROR": "任务执行失败；未公开内部异常或敏感诊断信息。",
    "WORKFLOW_ERROR": "任务未完成；无法确认具体原因，未生成成功结论。",
    "DEMO_UNSUPPORTED_REQUEST": (
        "Offline demo supports only 100 customers, comparing 1 and 3 cashiers；"
        "其他目标请使用 model 规划器。"
    ),
    "R1_REQUEST_LIMIT_OR_STOP": "本批验收请求预算已耗尽或已停止，不再发送模型请求。",
}


def public_message(code: str) -> str:
    populations: dict[str, tuple[ScenarioId, str]] = {
        "INVALID_REQUEST_COUNT": ("server_load_balancing", "请求数量"),
        "INVALID_CUSTOMER_COUNT": ("queue", "顾客数量"),
    }
    if code in populations:
        scene, label = populations[code]
        return (
            f"{label}必须是 1 到 {SCENARIOS[scene].maximum_entities} 之间的整数；"
            "请修改数量。本次未执行模拟。"
        )
    return _MESSAGES.get(code, _MESSAGES["WORKFLOW_ERROR"])


def invalid_explicit_population(query: str) -> str | None:
    """Only an unambiguous explicit quantity plus schema failure is evidence.

    Multiple different populations or mixed scenes are left unclassified. This
    is called only after a real/model adapter response has no native tool calls.
    """
    specifications: tuple[tuple[ScenarioId, str, str, str], ...] = (
        ("queue", "customers", "run_simulation", "INVALID_CUSTOMER_COUNT"),
        ("server_load_balancing", "requests", "run_server_simulation", "INVALID_REQUEST_COUNT"),
    )
    matches = [(spec, explicit_populations(query, spec[0])) for spec in specifications]
    populated = [(spec, values) for spec, values in matches if values]
    if len(populated) != 1:
        return None
    (_, field, name, code), values = populated[0]
    try:
        numbers = {Decimal(value) for value in values}
        if len(numbers) != 1:
            return None
        number = numbers.pop()
        value = int(number) if number == number.to_integral_value() else float(number)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    annotation = tool_contract(name).input_model.model_fields[field].rebuild_annotation()
    try:
        TypeAdapter(annotation).validate_python(value)
    except ValidationError:
        return code
    return None


def public_errors(
    errors: Sequence[JSONDict], *, feedback: JSONDict | None = None
) -> list[JSONDict]:
    """Whitelist codes/messages, including legacy errors lacking a code.

    No original message, exception class, user text, or provider metadata is
    copied. An existing terminal feedback code resolves legacy generic entries.
    """
    known = {*_MESSAGES, "INVALID_REQUEST_COUNT", "INVALID_CUSTOMER_COUNT"}
    fallback = (feedback or {}).get("code")
    result: list[JSONDict] = []
    seen: set[str] = set()
    for error in errors:
        raw = error.get("code", "WORKFLOW_ERROR")
        code = raw if isinstance(raw, str) and raw in known else "WORKFLOW_ERROR"
        if code == "WORKFLOW_ERROR" and isinstance(fallback, str) and fallback in known:
            code = fallback
        if code not in seen:
            result.append({"code": code, "message": public_message(code)})
            seen.add(code)
    return result


def task_output(status: str, report: str, errors: Sequence[JSONDict]) -> tuple[str, str]:
    """Shared CLI/R1 presentation; callers supply only a verified public report."""
    public_status = status if status in {"completed", "paused", "failed"} else "failed"
    stdout = f"任务状态：{public_status}\n"
    if public_status == "completed" and report:
        stdout += report.rstrip() + "\n"
    stderr = "".join(f"{e['code']}: {e['message']}\n" for e in public_errors(errors))
    return stdout, stderr
