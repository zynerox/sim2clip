# Sim2Clip MCP Server

MCP Server 使用锁定的官方 MCP Python SDK，通过真实 stdio 协议运行。
四个工具提供 Queue 与 Server 的单次及比较实验；输入输出 Schema 来自共享类型契约。
Server 负责参数验证、路由和序列化，数值计算由独立模拟引擎完成。
[多场景编排](multi-scenario-agent-m3.md) 说明工具发现、类型解析及 Agent 集成。

## 为什么选择底层 Server

输入 Schema 必须直接是 SimulationConfig 或 ComparisonRequest 的 JSON Schema。
因此采用 SDK 的低层 Server 注册工具及调用处理器，直接使用已有模型的
model_json_schema()，避免从参数列表生成另一份业务参数模型。
Server 负责验证、路由、错误映射和序列化；SimPy 引擎负责全部数值计算。

官方说明：[低层 Server](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/)、
[Client](https://py.sdk.modelcontextprotocol.io/client/)。

## 文件与启动

```text
src/sim2clip/mcp_server/
├── __init__.py
├── __main__.py
└── simulation_server.py
examples/mcp_client.py
tests/mcp/
├── conftest.py
├── test_tools.py
├── test_stdio.py
└── fixtures/engine_failure_server.py
```

安装项目后运行 `python -m sim2clip.mcp_server`，也可使用 sim2clip-mcp 入口。
等价地可运行 `python -m sim2clip.mcp_server.simulation_server`。
这是 stdio Server，等待 MCP Host 的 JSON-RPC 消息；正常日志只在 stderr。
导入模块仅定义函数，不启动进程、不监听端口、不写文件。

客户端示例通过 sys.executable 启动相同 Python 环境中的模块，在上下文管理器
退出时关闭连接及 Server 子进程。读超时为 30 秒。

```powershell
uv run python examples/mcp_client.py --output outputs/mcp-demo
```

它执行真实工具发现、两个工具调用，验证响应后保存 run.json 和 comparison.json。

## 工具契约

### run_simulation

输入 arguments 为 SimulationConfig 的字段，使用平铺结构：

```json
{"cashiers": 3, "customers": 100, "seed": 42}
```

默认值、取值范围和未知字段策略全部来自 SimulationConfig。
不包裹 config 键，不通过 Server 的独立参数表复写默认值。
返回 SimulationResult，包含 metrics、events、样本、记录和复现元数据。

### compare_simulations

输入 arguments 为 ComparisonRequest：

```json
{
  "experiments": [
    {"cashiers": 1, "customers": 100},
    {"cashiers": 3, "customers": 100}
  ],
  "seeds": [42, 43, 44, 45, 46]
}
```

调用 run_comparison(request.experiments, seeds=request.seeds)，返回 ComparisonResult。
配对样本、预算、重复种子和混杂参数的检查来自既有 ComparisonRequest，
Server 不实现另一套比较逻辑。

### 成功响应

tools/list 发布输入 Schema 和成功输出 Schema。成功时 isError=false，
structuredContent 是完整模型的 model_dump(mode="json") 结果；content 同时包含
相同数据的 JSON 文本，便于模型及旧式 Host 读取。JSON 序列化拒绝非有限数值。

官方 SDK 2.x 的 Python 属性名为 input_schema、output_schema、structured_content、
is_error；协议线上分别使用 inputSchema、outputSchema、structuredContent、isError。
业务字段如 timestamp 和 event_type 不改变。

### run_server_simulation / compare_server_simulations

分别使用 `ServerSimulationConfig → ServerSimulationResult` 和
`ServerComparisonRequest → ServerComparisonResult`，参数直接作为 arguments。
单次示例：

```json
{"requests": 1000, "servers": [{"server_id": 1}, {"server_id": 2}], "routing_strategy": "least_outstanding", "seed": 42}
```

比较使用稳定 group_id，每组包含 config，声明 `routing_strategy` 或 `server_pool` 单一比较轴，
组内 config.seed 等于 seeds[0]。完整配置、事件与统计规则见 [Server 模型](server-load-balancing-m1.md)。
MCP 不接受外部样本或模型生成结果；服务器场景 Agent 验证入口要求 workload_source="generated"。Queue 旧比较继续使用 supplied 标签，并独立复核共享样本的声明种子。

`examples/multi_scenario_mcp.py` 通过真实 stdio 发现并调用四个工具，执行独立验证后保存四份 JSON。
客户端按 Registry 中的精确类型解析；缺失、重复、未注册、Schema 不一致或无描述的发现结果直接失败。
isError=true 的结构化错误和 JSON 文本错误均保留错误码；不会把成功形状的错误响应当成结果。

## 错误契约

所有工具级失败返回 isError=true。structuredContent 与 content 中的 JSON 文本一致，
采用协议错误信封（不是另一套模拟 Schema）：

```json
{
  "error": {
    "code": "INVALID_PARAMETERS",
    "message": "Arguments do not satisfy the shared input schema.",
    "retryable": false,
    "issues": [
      {"location": ["customers"], "type": "greater_than_equal", "message": "Input should be greater than or equal to 1"}
    ]
  }
}
```

| code | 阶段 | Agent 处理方式 |
| --- | --- | --- |
| INVALID_PARAMETERS | 输入 Pydantic 校验失败，尚未执行引擎 | 将字段位置反馈给 Planner，在修复预算内修改计划 |
| ENGINE_ERROR | 引擎抛出异常 | 保存错误并停止该次实验，依据日志定位原因 |
| INVALID_RESULT | 返回值不符合共享模型或无法序列化 | 停止，禁止依据该响应生成结论 |
| UNKNOWN_TOOL | 工具名称未注册 | 保存错误并终止任务；检查工具名称及发现结果后创建新任务 |

执行异常的详细 traceback 进入 stderr，工具响应使用稳定错误信息。
输入错误提供 location / type / message，剔除原始输入值和异常对象，保证 JSON 可序列化。
成功输出 Schema 约束成功结果，错误信封只在 isError=true 时返回。

retryable=false 表示不应原样重复确定性错误。客户端连接断开或超时是另一类传输失败，
由 Client / Agent 在预算内处理。参数错误被修复后可以再次调用。
Server 本身不自动修复参数、重试实验或生成替代数据。

## 执行与边界

计算通过线程调用独立引擎，协议事件循环继续处理 MCP 会话。
每次引擎执行拥有自己的环境、资源和事件记录器；Server 无全局实验状态。
当前 Server 无持久化缓存，重复请求重算并产生相同结果及 ID。

tools 的只读、幂等、封闭世界提示描述当前纯计算行为。Server 不接收任意脚本、
不提供目录操作、不生成报告。结果事实校验及是否追加实验由 Agent 工作流决定。

## 测试

```powershell
uv run pytest tests/mcp
uv run pytest
uv run ruff check .
uv run mypy
```

test_tools 使用 SDK 的内存传输，仍走协议协商、工具发现和 tools/call，而不是
直接调用业务函数。正常响应与直接引擎执行对照，确认没有另一套计算实现。
test_stdio 启动独立 Python 子进程，并从其他工作目录调用已安装模块。
失败注入仅在测试夹具中出现，覆盖真实 stdio 错误传递和继续调用。

## Agent 状态与恢复

Agent State 保存问题、计划、验证结果、工具历史、结果引用和持久化预算。
具体字段及恢复语义见 [Agent Workflow](agent-workflow.md)。

Checkpoint 只保存可序列化数据，不保存 MCP 会话、进程、线程或数据库连接。
完整事件位于外部产物；State 保存引用及内容哈希。
Executor 先检查 `isError`，再将成功响应解析为共享模型。
Result Validator 独立复核事件、指标及证据充分性，决定生成报告或追加配对种子。
