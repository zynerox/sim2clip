# MCP 与 LangGraph 多场景编排

Queue 与 Server 使用独立模型，通过静态 Registry 和场景适配器接入同一五节点工作流：
Planner → Parameter Validator → MCP Executor → Result Validator → Report Generator。

本文说明工具发现、原生 Tool Calling、设计锁定、场景验证及旧 Checkpoint 兼容。
运行记录为该版本的本地协议验收，使用模型夹具；
真实远程模型验收见 [Qwen3.5-Plus 验收](real-llm-r1-verification.md)。
## 1. Registry 与协议边界

`scenario_registry.py` 使用冻结 dataclass 和只读 MappingProxyType。只有 queue / server_load_balancing 两个场景。Registry 位于包顶层公共入口，保持 scenarios 内服务器契约对 Queue Schema 的独立性；既有架构边界测试没有放宽。ScenarioDefinition 保存说明、能力、局限、默认假设、最大实体/比较预算、工具契约、独立验证和报告入口；参数边界以精确 Pydantic Schema 为准，不复制一份万能参数表。ToolContract 保存名字、场景、描述、输入/输出具体类型和比较标识。

| 工具 | 输入 | 输出 | 计算模型 |
| --- | --- | --- | --- |
| run_simulation | SimulationConfig | SimulationResult | 原共享 FIFO Queue |
| compare_simulations | ComparisonRequest | ComparisonResult | 原 Queue 配对比较 |
| run_server_simulation | ServerSimulationConfig | ServerSimulationResult | M2 每服务器独立 FIFO |
| compare_server_simulations | ServerComparisonRequest | ServerComparisonResult | M2 两轴配对比较 |

Registry 只存元数据/类型与惰性适配回调，绝不导入或运行引擎。只有 MCP Server 的明确四分支 handler 调用已有计算接口；线程封装只用于避免阻塞协议循环。原两工具描述、Schema 和处理规则保持兼容。

Client 必须发现恰好四个已登记工具，逐项核对 input/output Schema 与非空描述；缺失、重复、额外工具和契约不一致直接报错。成功结果按工具名选择具体不可变模型，JSON 字典只是传输形式。isError=true 必须作为错误处理，支持结构化和 JSON 文本错误信封；没有可解析错误时给稳定 TOOL_ERROR。MCP 2.3 自带输出 Schema 校验的 RuntimeError 映射为 INVALID_RESULT，避免把确定性形状错误误当可重试传输异常。

## 2. 动态工具选择与宿主约束

ModelPlanner 从真实 MCP list_tools 获取名字、描述和输入 Schema，将原 request Schema 原样嵌入规划信封，保留根 $defs。LangChain 模型通过 bind_tools / ainvoke 返回真正的 AIMessage.tool_calls；Planner 消费这些调用，验证目标、理由、假设和 request，转换为实际平铺 MCP 参数。AIMessage / ToolMessage ID、provider ID、调用轮次和反馈持续保留。

自然语言不由关键词选择工具。`query_validation.py` 只在模型已选工具后，拒绝明确的跨场景冲突、人口数量矛盾、容量组矛盾、RR/LO 问题采用错误比较轴，以及明确要求的未支持机制。它不生成参数或工具计划。明确排除的故障/延迟列表不触发能力拒绝。未知表达仍依赖模型依据能力说明拒绝；该守卫不是完整的自然语言语义证明器。

模型无工具计划时明确给出 UNSUPPORTED_REQUEST 和支持范围，不回退 Demo。错误场景或参数在参数预算内返回模型重新选择；不支持的已明确需求终止，不执行模拟。DemoPlanner 仅支持明确的 100 顾客、1/3 收银台官方案例，含服务器/请求的问题不能套用相同数字。当前没有 Server DemoPlanner。

首次合法计划锁定场景、契约版本、结构化目标、工作负载、比较轴、组 ID/顺序/服务器池/策略和主指标。Queue 主指标为平均等待，服务器为平均响应；首组为基线，P95 单独披露。补充实验只允许保留原种子并追加，不能更换人群/分布/资源/策略/目标或指标。服务端配对公平性与 Agent 锁定是独立的两层检查。服务器 batch + constant 不含随机性，一种子足够；其余默认至少五个配对种子。描述性预算不构成显著性保证。

## 3. State、证据与恢复

State 使用 state_version=2、scenario_id（合法计划前 null）、schema_version=1。旧字段名全部保留，包括 customer_runs：它现在同时统计顾客或请求 run 工作量，100000 总预算、各类 retry_counts 与 tool-call 上限继续持久化。simulation_results 只增加场景/工具元数据及服务器 group_id；所有事件、样本与完整记录仍在外部 JSON。

`checkpoint_compat.py` 从旧状态里的已验证请求、工具历史和 Queue cashier 设计推断场景，补充版本、目标/轴/指标元数据。每个原节点入口执行兼容投影，并随该节点正常更新落盘；恢复仍使用 ainvoke(None)，不伪造输入、不改变待执行边、不重写旧 Checkpoint 行。已完成任务在返回兼容投影前同样复核原始证据和收据，不重新执行工具或写入历史。未知版本、混合场景或场景与工具历史冲突明确失败。

跨版本恢复使用真实旧代码夹具 `tests/agent/fixtures/pre_m3_f16ed28.zip`，
SHA256 为 `f4f8323d1fd9944dfa6b0b03525c67a23bae959626868239e1e4c1d9b683b775`。
旧进程生成 SQLite，新进程在五个节点及完成态恢复，检查结果并确认原 checkpoint 行未被覆盖。
测试不需要分发私有 SQLite 或依赖本地历史 Git 对象。

结果验证、报告和完成态恢复边界都复核：已验证工具批次、锁定设计及种子覆盖、原子收据的参数/引用、文件 SHA-256、实际 result IDs、State 中的配置/指标/比较摘要。还从 Schema 重新计算所有历史尝试的工作成本、核对 State 总数与持久化工作/工具预算。每份原始 JSON 重新解析精确结果类型，再调用原 Queue Validator 或 M2 独立服务器 Validator。服务器负载均衡场景的 MCP 请求只允许 generated 样本来源；standalone 的 supplied 手算样本仍然支持，但不能冒充 MCP 采样结果。

Queue 的原独立 Validator 保持原样，额外 MCP 来源检查从标准库随机流复核声明 seed 的样本：单次来源为 generated，旧比较引擎把共享生成样本注入每组，因此合法来源仍为 supplied；不能统一改成 generated。

事件/指标不一致在预算内重新获取证据；报告前缺失或改动证据则明确失败。JSON 文件必须与 SQLite 同时保留。仍为至少一次计算 + 确定性幂等结果，非分布式恰好一次。SHA 用于一致性检测，不是对恶意同时改写全部证据的签名保证；本地 SQLite/目录是可信持久化边界，同一任务不支持并发写入。

## 4. 实际运行记录

未进行远程 API 调用。使用 Mock BaseChatModel 的原生 AIMessage.tool_calls，以及本机 OpenAI-compatible HTTP 协议夹具驱动真实模型 SDK。MCP、SimPy、LangGraph、SQLite 与跨进程恢复均实际执行；这证明工具协议和宿主流程，不证明外部模型对任意自然语言的识别质量。

下面列出保存的四工具调用结果；固定输入示例可使用真实 stdio 重新执行，无需 LLM。

| 工具 | 真实结果标识 | runs / events |
| --- | --- | --- |
| run_simulation | a9a3ebb09799c82b961f3cfd55964993b360fe1ed23c04023dc488528c37c84d | 1 / 9 |
| compare_simulations | de7c978f652d2aec4a124a78e63990daa6d06461544da368a69cdf55a1e63438 | 2 / 18 |
| run_server_simulation | 8669296b1c4dd109985ae82a9336f5110c9a92f576949007b099929816efd7b8 | 1 / 12 |
| compare_server_simulations | 6cd5d4d67ac8269b6b6061eaec0f9e9bceae9563b792509d636f45a87f6a0bda | 4 / 480 |

| Workflow | 原生规划轮数 / 工具数 | 真实验证后结果 |
| --- | --- | --- |
| 100 顾客，1/3 收银台 | 1 / 1 | completed；5 种子平均等待 789.5201388775291 / 33.27183721133241 秒 |
| 1000 请求，RR/LO，两台速率 1 | 2 / 2 | completed；seed42 试验不足后保留设计追加 43–46；RR/LO 平均响应 1.4903879876983468 / 1.4674586604073412 秒 |

服务器各次 P95 均值分别为 3.0338492526870846 / 2.786753677552226 秒；不是合并 P95，也不保证 LO 对其他负载或异构池胜出。决策、工具历史、锁定设计、摘要和结果引用由 State 保存。

测试中的恢复/拒绝案例：服务器问题第一轮选 Queue 工具时，宿主返回 INVALID_PARAMETERS ToolMessage，模型下一轮选择 compare_server_simulations，MCP 实际只执行合法服务器调用；故障、库存及超时重试问题给 UNSUPPORTED_REQUEST，无工具执行；修改目标/组/负载/轴/种子计划拒绝；原生模型用合法参数修复非法整数、容量、未知策略；被篡改的服务器事件、记录、指标、摘要、差值和哈希被独立验证拒绝。

CLI 跨进程恢复覆盖完整五种子实验和一到五种子追加。已有结果足够时恢复无需模型凭据，
追加规划仍需要配置。当前 CLI 按场景生成报告、结果及离线 HTML；
Server GIF 返回 unsupported，详情见 [Server 回放](server-visualization-m4.md)。

## 5. 检查命令与结果

已保存的多场景编排验收为 **508 passed，0 失败 / 错误 / 跳过**，
包含 18 项真实浏览器回归；Ruff、格式检查及 strict mypy 通过。
测试覆盖四工具协议、设计锁定、错误反馈、来源复核、持久化预算和跨进程恢复。
这组本地验收不包含远程模型请求。

```powershell
uv run pytest tests/agent/test_multi_scenario.py tests/agent/test_multi_scenario_evidence.py -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\mypy.exe src/sim2clip examples/multi_scenario_mcp.py
.\.venv\Scripts\python.exe examples/multi_scenario_mcp.py --output outputs/multi-scenario-mcp
```

四工具示例使用固定输入、真实 stdio 与独立验证，不调用 LLM；已有输出文件拒绝覆盖。真实模型用法见 README/CLI 文档，需用户配置支持原生工具调用的模型。本地协议验收未使用远程模型凭据。

## 6. 可视化数据接口

Server 可视化消费 ServerSimulationResult / ServerComparisonResult JSON，与引擎和 Agent 编排分别处理。单次结果有 scenario、schema_version、config、request_samples、request_records、metrics、events、metadata、workload_source、workload_hash 与 result_id。比较结果的 results 是 (group_id, seed, result) 包装；request.experiments 定义稳定组配置，seeds 定义配对，summaries/paired_differences 保留原类型。

事件为判别联合 request_arrived → request_routed → request_started → request_completed；读取 sequence 的规范顺序，同刻完成所有事件后绘制。完成 → 旧队列启动 → 请求按 ID 到达/分流/立即启动的业务优先级仍遵循 M1/M2。各节点 active 固定 0/1，等待队列长度不含 active；全局峰值使用 settled 队列状态，不能用瞬时 routed/started 中间值制造假峰值。

回放可根据 sample/record 显示工作量、目标服务器、等待/处理中/完成状态，展示各服务器独立 FIFO 和速率；RR cursor/LO 在途数能从事件重放推导，不额外虚构路由。A/B 按相同 seed 和 workload_hash 选取，使用共享模拟时钟；该 seed 的 P95 与多种子 P95 均值分别标注。

State 只提供外部 JSON 的路径、文件 SHA、result_id、group_id、配置和指标。浏览器按场景分派结果，并按事件标签校验判别联合 Schema；Server 不复用 Queue 的业务事件语义。可视化不应把网络延迟、失败、重试等未建模行为画成计算事实。

模型语义质量不能由上述本地协议验收证明；已完成的真实远程评测及其边界见 [真实 LLM 验收](real-llm-r1-verification.md)。
