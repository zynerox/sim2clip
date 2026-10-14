# 多场景兼容性与稳定性验收

本文汇总保存的 M5 本地协议、端到端、恢复、文件完整性及浏览器验收。
该次记录为 **599 passed，0 失败 / 错误 / 跳过**；较新的完整工程记录见
[Presentation Mode](presentation-mode-7.4.md)。

Server 使用本机 HTTP 夹具驱动真实模型 SDK，Queue 使用固定 DemoPlanner；
另有 Mock AIMessage Tool Calling 测试。MCP、SimPy、LangGraph、SQLite 与独立验证实际执行。
这些本地测试不请求远程 LLM，不能证明模型对任意自然语言的理解能力。
远程结果见 [真实模型验收](real-llm-r1-verification.md)。

## 1. 验收环境

| 项目 | 实际环境 |
| --- | --- |
| 操作系统 | Windows 11，build 10.0.26100 |
| Python | 3.12.14；声明最低 3.11，本机未执行 3.11 测试 |
| MCP / AnyIO | mcp 2.3.0 / anyio 4.15.1 |
| 编排 | langgraph 1.2.14，langgraph-checkpoint-sqlite 3.1.1 |
| 模型 SDK | langchain-openai 1.7.0，langchain-core 1.6.9 |
| 模型契约/计算 | pydantic 2.14.0，simpy 4.1.2 |
| 测试 | pytest 9.1.1，Playwright 1.63.0，Chrome 155.0.8059.39 |
| 质量 | Ruff 0.16.10，mypy 1.20.2，strict=true |

## 2. 端到端验收矩阵

| 链路 | 实际路径/验证 | 结果 |
| --- | --- | --- |
| Queue 官方 CLI | 100 顾客、1/3 收银台、seeds 42–46；DemoPlanner 先 pilot，再补足五种子 | completed，HTML/GIF/JSON/report/timeline |
| Server 容量 CLI | 1000 请求、1/3 台、各节点 rate=1、RR、五配对种子 | completed，HTML/JSON/report/timeline |
| Server 策略 CLI | 1000 请求、两节点 rate=1/1、RR/LO、五配对种子 | completed，HTML/JSON/report/timeline |
| Server 异构 CLI | 1000 请求、两节点 rate=1/2、RR/LO、五配对种子 | completed，HTML/JSON/report/timeline |
| 四工具 | 安装后的 wheel 在独立子进程发现 Schema 并逐个调用，复核返回值 | 四个成功，无源码目录资源依赖 |
| 原生模型消息 | Mock AIMessage 选择 Queue / Server；HTTP 夹具驱动真实模型 SDK | 通过，未使用关键词分支替代 tool_calls |
| 错误/拒绝 | 选错场景、非法参数、工具失败、篡改响应、未支持机制 | 有界重规划或明确失败，无 Queue 回退 |
| 实验锁定 | 场景、工作负载、组配置和指标改变；追加合法种子；预算越界 | 不合法设计拒绝，合法补种子通过 |
| 恢复/收据 | 真正旧 SQLite、旧源代码边界、新 Server 新进程恢复、收据缓存 | 原历史不重写，已落盘工具响应不重复计算 |
| 浏览器 | Queue / Server file://、播放/暂停/倍速、组/Seed、正反 Seek | 56 项 Chromium 自动测试通过 |
| 历史产物 | 早期导出 JSON、CLI 与多场景 SQLite 只读复制 | 重导出/恢复通过，源文件 SHA 未变 |
| 安全/发布 | 私密 canary、目录越界、占用文件、写盘故障、聚合篡改 | 拒绝泄漏/越界/假成功，用户文件保留 |

CLI 链路覆盖规划、参数验证、真实 stdio 工具、独立核验、报告与导出。
下面的性能测量单独调用引擎，不属于 Agent / MCP 端到端计时。

## 3. 实验结果与产物

全部比较使用 seeds `[42, 43, 44, 45, 46]`，每个 seed 内组间共享真实样本。单位为模拟秒；以下服务器数字均为五次指标的算术均值。

| 实验/组 | 平均响应 | 每次 P95 均值 | 平均等待 | 队列峰值均值 | 完成时间均值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 容量 one：1 台 RR | 7.797612 | 14.731471 | 7.772327 | 612.0 | 25.291996 |
| 容量 three：3 台 RR | 0.107930 | 0.307438 | 0.082645 | 25.2 | 10.063963 |
| 策略 rr：rate 1/1 | 1.490388 | 3.033849 | 1.465103 | 227.2 | 13.179144 |
| 策略 lo：rate 1/1 | 1.467459 | 2.786754 | 1.442174 | 225.0 | 12.820104 |
| 异构 rr：rate 1/2 | 0.759151 | 2.607618 | 0.740154 | 117.6 | 12.749814 |
| 异构 lo：rate 1/2 | 0.065660 | 0.200179 | 0.048349 | 23.4 | 9.983738 |

Queue 五种子平均等待：1 收银台 **789.520139**，3 收银台 **33.271837**；队列峰值均值 63.8 / 11.2，与历史官方基线一致。页面 seed 42 单次等待仍为 745.18 / 20.59 秒，分别标注单次及汇总。

服务器 P95 列不是 pooled P95。策略实验的差异只是当前样本的数值观察，不作显著性结论；异构实验需保留 rate 1/2 的条件，不能与同构实验跨表归因。LO 不保证胜于 RR。结果及报告继续说明网络路径示意、零传输耗时和非生产性能预测。

CLI 含协议/验证/落盘/导出的总耗时：Queue 7.797 秒，容量 22.503 秒，策略 22.317 秒，异构 22.718 秒。每条另在新进程执行 `resume`，保持 completed，tool history 不增长，HTTP 夹具不增加模型轮次。

| 场景 | 成功导出 |
| --- | --- |
| Queue | visualization.html、demo.gif、demo.frames.json、result.json、report.md、agent-timeline.json、公开 state.json / decisions.json、graph.mmd |
| Server | visualization.html、result.json、report.md、agent-timeline.json、公开 state.json / decisions.json、graph.mmd |

Server JSON 摘要标注 GIF `status=unsupported`，没有 demo.gif，导出仍成功。GIF 真实失败与不支持分开；现存不相关 GIF 不被清理。State 保存引用及摘要，原始样本、记录和事件在外部文件，未塞进 Checkpoint。

无需安装的保存结果演示见 [Demo 与离线包](release-demo.md)。

## 4. 四工具与错误处理

| 工具 | 输入 / 输出 | 实际验证 |
| --- | --- | --- |
| run_simulation | SimulationConfig / SimulationResult | 三顾客单次，原工具名称和契约未改 |
| compare_simulations | ComparisonRequest / ComparisonResult | 1/3 收银台，配对结果独立复核 |
| run_server_simulation | ServerSimulationConfig / ServerSimulationResult | 三请求单次，四事件生命周期 |
| compare_server_simulations | ServerComparisonRequest / ServerComparisonResult | 同节点数量 RR/LO，request/result 独立复核 |

协议测试覆盖工具发现和 Schema、原生 Tool Calling、执行前场景检查、无工具拒绝、
合法追加种子、设计锁定与预算。证据测试覆盖事件、指标、收据和摘要篡改，
工具异常重连、原子收据及 completed 恢复后的来源复核。
HTTP 夹具只提供固定响应，不充当开放自然语言模型；Agent 不直接调用引擎。

## 5. 结果与 Checkpoint 兼容性

| 保存资料 | 验证结果 |
| --- | --- |
| 旧 Queue CLI SQLite | 缺少场景 / 状态版本时兼容恢复为 queue / State v2 / Schema v1，原工具历史不变 |
| 旧多场景 SQLite | Queue 与 Server 在新进程恢复 completed，原调用数不增加 |
| [公开 Queue 结果](assets/demo-result.json) | 原结果字节不变，独立验证、HTML 与 21 帧 GIF 导出通过 |
| 旧代码夹具生成的六类状态 | 五个节点及完成态的跨版本恢复通过，原 checkpoint 行不覆盖 |
| Server pause / resume | 新进程追加所需配对种子，已保存调用不重复 |

工具结果在 State 中保存为引用和摘要；完整状态还包含用户问题、规划及消息记录。
恢复需要 SQLite 与原工具响应文件。
公开 state.json 是展示引用，不能代替完整恢复资料。
SHA256 用于检测一致性，不是对同时篡改全部证据的数字签名。

## 6. SDK / AnyIO 取消停滞专项

Windows 中观察过偶发 SDK / AnyIO 取消停滞；后续没有稳定复现，**根因未确认，不能认定已修复**。
隔离进程检查覆盖正常调用、SDK deadline、task.cancel、AnyIO scope、asyncio.timeout、
子进程中途退出与发现阶段缺失工具。八用例专项及两次重复共 24 次通过。
退出后检查 Server 进程、异步任务和线程，并确认重连后的健康调用成功。

这些检查不排除高负载、大响应或 Windows 管道退出竞态。
超时保护只能限制资源占用，不能代替死锁修复；生产工具超时与 Agent 预算没有因此放宽。

## 7. 公开输出与文件边界

公开 CLI 输出使用已验证结果和固定展示说明，不复制模型原始消息、请求头、provider ID 或任意模型 reason。
SQLite、原始工具响应与 `--skip-visualization` 审计输出仍可能包含用户自由文本，应作为本地恢复资料管理。
直接 Python 渲染 API 接受调用方提供的文本，不提供通用秘密识别保证。

损坏结果、契约不符、哈希不符或不支持场景会明确报错；失败 Workflow 不导出成功演示。
产物路径检查拒绝越界和占用文件。新目录整体 rename；既有目录遭强杀或断电时不具备完整文件系统事务保证。
CLI 退出码与完整产物应同时检查，不能将目录存在视为成功。

## 8. 性能与资源边界

单进程 probe 分开测计算、独立验证、序列化和 HTML 打包；不将这些耗时称为 Agent/MCP E2E。字节为紧凑 JSON 与内嵌 HTML（CLI result.json 缩进后更大），内存为实际 Python worker 的 Windows OS peak working set，包含导入、模型和序列化，不是浏览器/整机峰值。

| 场景/规模 | 计算秒 | 独立验证秒 | 紧凑 JSON 字节 | HTML 字节 | Python 峰值 MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Server 3 | 0.001480 | 0.001017 | 4984 | 87695 | 97.52 |
| Server 1000 | 0.046302 | 0.034036 | 1262424 | 1434859 | 123.16 |
| Server 10000 | 0.675769 | 0.419747 | 12779360 | 13761699 | 322.57 |
| Server 100000 request-runs | 10.123342 | 5.976868 | 126796467 | 未做完整上限 HTML | 1175.69 |
| Queue 3 | 0.000664 | 0.000068 | 2905 | 69478 | 97.69 |
| Queue 1000 | 0.031861 | 0.004802 | 721422 | 843827 | 114.31 |
| Queue 10000 | 0.526907 | 0.059592 | 7279767 | 7906172 | 255.74 |

**100000 request-runs = 10000 请求 × 两组 × 五种子，共 400000 事件。**
受控测量设置 deadline=180 秒、RSS cap=1.5 GiB，实际 18.466 秒，无保护触发，验证和序列化完成。
上限紧凑 JSON 约 120.92 MiB；完整 HTML 约 128–134 MiB 仅为估算。
未验证该上限的完整 MCP 解析、Agent 落盘和浏览器初始化；多份数据可能同时占用内存。
这些单机测量不构成资源保证或 SLA。

实际 Chrome file:// 测量：初始化包含导航、数据检查、构建播放器；Seek 为 30 次正反跳转及绘制调用耗时。仅是本机样本，不是统计 SLA。

| 场景/规模 | 初始化秒 | Seek 中位数/最大 ms | 当前 JS heap 字节 |
| --- | ---: | ---: | ---: |
| Server 3 | 0.120 | 0.3 / 0.9 | 2423160 |
| Server 1000 | 0.332 | 0.4 / 1.8 | 3539364 |
| Server 10000 | 2.156 | 1.0 / 2.5 | 19090140 |
| Queue 3 | 0.173 | 0.8 / 2.5 | 2549360 |
| Queue 1000 | 0.192 | 0.5 / 1.8 | 12614980 |
| Queue 10000 | 0.383 | 1.55 / 2.4 | 54274808 |

JS heap 为 CDP 当时值，不是峰值；未测浏览器 OS 峰值。
初始化包含导航、数据检查与播放器构建，Seek 为 30 次正反跳转及绘制调用。

## 9. 安装与离线检查

保存的来源版本 0.1.0 wheel 验收使用独立 Windows / Python 3.12 环境，
实际检查四工具、CLI 入口与两场景导出；Python 源文件及静态资源与对应源码逐字节匹配。
另在排除原 venv 的源代码快照中执行 locked / offline 安装、固定 Demo 和 stdio 测试。
该安装使用已有依赖缓存，未验证全新无缓存机器的网络下载，也未覆盖 Linux / macOS / Python 3.11 实机。

离线 HTML 检查覆盖嵌入资源、文件加载、播放、暂停、组 / Seed、正反 Seek 和 Python / JS 状态一致性。
源码中的 page.html 是模板，不能直接当成完整导出页面打开。

## 10. 检查命令

以下使用未被占用的新目录；脚本拒绝覆盖已有输出。
`legacy` 还需要本地保存的旧实验及恢复资料，公开离线包不能代替这些输入。

```powershell
uv run python scripts/verify_m5.py suite --output outputs/m5-repeat/suite
uv run python scripts/verify_m5.py e2e --output outputs/m5-repeat/e2e
uv run python scripts/verify_m5.py legacy --output outputs/m5-repeat/legacy
uv run python scripts/verify_m5.py performance --output outputs/m5-repeat/performance
uv run python scripts/verify_m5.py performance --queue --output outputs/m5-repeat/queue-performance
uv run python scripts/verify_m5.py browser --source outputs/m5-repeat/e2e --output outputs/m5-repeat/browser
uv run pytest tests/mcp/test_cancellation.py -q -o junit_family=legacy
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## 11. 限制

| 项目 | 判断 |
| --- | --- |
| 公开自由文本、伪造聚合、目录越界及部分发布包 | 已修复并回归 |
| SDK / AnyIO 偶发取消停滞 | 未稳定复现，根因未确认，不能标为已修复 |
| Linux / macOS、Python 3.11 实机 | 未验证 |
| 最大预算的完整 MCP / Agent / HTML 链路 | 仅计算与独立验证上限实测，未承诺全链路资源或 SLA |
| Server GIF | unsupported；离线 HTML 和发布 MP4 可用 |
| 多文件发布遭强杀或断电 | 新目录使用 rename；既有目录不具备完整事务保证，硬中断后需要检查产物 |
| 大响应资源开销 | 内存及重复解析仍需进一步测量 |

真实模型模式需要 MODEL_API_KEY、Chat-Completions 兼容 MODEL_BASE_URL
以及支持 function/tool calling 的 MODEL_NAME。
该次本地验收未发送远程模型请求。
