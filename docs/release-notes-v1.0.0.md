# Sim2Clip v1.0.0 Release Notes

Sim2Clip turns natural language questions into reproducible simulation experiments.

Sim2Clip 将自然语言问题转化为可复现、可独立核验的有限模拟实验。
LLM 负责实验规划，Simulation Engine 负责计算事件与指标，Independent Validator 负责结果核验。

[Repository](https://github.com/zynerox/sim2clip) · [v1.0.0 Release](https://github.com/zynerox/sim2clip/releases/tag/v1.0.0)

## 核心能力

- LangGraph 编排规划、参数检查、MCP 执行、独立核验和报告，支持有界反馈与 SQLite checkpoint 恢复。
- 真实 stdio MCP Server 提供四个类型化工具：`run_simulation`、`compare_simulations`、
  `run_server_simulation`、`compare_server_simulations`。
- Queue 使用共享 FIFO；Server 使用每节点独立 FIFO，支持 Round Robin / Least Outstanding 与不同处理速率。
- 配对 A/B 在每个 Seed 使用同批样本，分别保存逐种子指标、多种子汇总与差值。
- 独立验证器复核样本、事件、生命周期、队列、容量和指标，不依赖模型判断数值正确性。
- 保存事件支持确定性回放；自包含 HTML 提供 Analysis、六阶段 Presentation、桌面与 9:16 布局。

## 下载与观看

Release 页面提供以下附件：

| 附件 | 内容 |
| --- | --- |
| `sim2clip-offline-demo-v1.0.0.zip` | Queue / Server HTML、观看说明、来源、MIT License 与内部 SHA256 清单 |
| `queue-presentation-v1.0.0.mp4` | Queue 竖屏展示，1080×1920、20 fps、30 秒，无音轨 |
| `server-presentation-v1.0.0.mp4` | Server 竖屏展示，同规格，无音轨 |
| `RELEASE_NOTES.md` | 本版本发布说明 |
| `SHA256SUMS.txt` | ZIP、两段 MP4 与配套发布说明的 SHA256；清单不校验自身 |

完整解压后，用 Chrome / Edge / Chromium 打开 `queue/visualization.html`
或 `server/visualization.html`。选择 **Presentation** 和桌面 / **9:16**，点击播放；
可暂停、拖动时间轴或切换 Seed。Analysis 可检查参数、样本、事件、多种子汇总和公开执行记录。
无需安装项目、API Key、模型服务、CDN 或 Web 服务。

这些媒体回放已保存的 ModelPlanner / Qwen3.5-Plus 实验，不是 DemoPlanner 录制，
不会重新调用 LLM 或执行模拟。**30 秒是展示时长，不是原任务执行时间。**
六阶段为问题 → 计划 → MCP → 独立核验 → A/B 回放 → 结果揭晓；
核验先于事件回放展示，不表示 Validator 先于引擎计算运行。

## 实验口径与来源

| 场景 | 配置 | Seed 42 平均值 | Seeds 42–46 平均值 |
| --- | --- | --- | --- |
| Queue | 100 位顾客，1 / 3 个收银台 | 等待 745.18 / 20.59 秒 | 等待 789.52 / 33.27 秒 |
| Server | 1000 请求，1 / 3 台服务器，Round Robin | 响应 7.1000 / 0.0876 秒 | 响应 7.797612 / 0.107930 秒 |

Queue 假设指数到达间隔均值 10 秒、服务均值 25 秒。
Server 假设指数到达间隔均值 0.01 秒、工作量均值 0.025 normalized work，
每节点 capacity=1、processing_rate=1 normalized work/s。
这些是模型假设，不是生产环境测量。

发布版本为 1.0.0，保存实验的来源应用版本为 0.1.0。
公开 HTML 省略可选运行时间等元数据；实验配置、样本、事件、指标、执行与核验结论及内嵌 JS/CSS 保持原值。
因此 HTML 和 ZIP 与原验收产物并非逐字节相同；两段 MP4 保持原字节。
`PROVENANCE.json` 记录来源版本、参数、结果与工具响应校验信息，以及公开包装方式。
包内清单校验五个有效负载文件；包外清单校验四个附件。
请使用与下载文件配套的校验清单，不将旧清单用于修订后的附件。

## Real LLM Validation

Qwen3.5-Plus 累计 **12 次真实请求**，覆盖 Queue + Server 和全部四个 MCP tools。
验收记录包含原生 Tool Calling、真实 stdio MCP 收据、LangGraph 节点和独立验证结果。

- 原始 R1：10 次请求，8 个支持任务的严格完整任务统计为 **6/8**，另含 E/F2 两个拒绝案例。
  原始 G/H 的工具、参数和计算结果正确，但单次报告误写为比较；原始结论保持不变。
- 修复后 G/H：额外 2 次真实请求，完整单次任务复验 **2/2**。
- E/F2 仅验证零工具安全拒绝；原始拒绝正文未保存，**未验证完整拒绝解释文本**。
- **真实验收未触发重规划**；本地反馈、恢复和失败分支测试不能替代远程模型证据。

12 次请求包含拒绝和复验，不等于 12 个不同成功任务。
案例、指标与边界见 [真实模型验收](https://github.com/zynerox/sim2clip/blob/main/docs/real-llm-r1-verification.md)。

## 已验证范围

已保存的完整工程验收为 **756 passed，0 失败 / 错误 / 跳过**；
范围见 [Presentation Mode](https://github.com/zynerox/sim2clip/blob/main/docs/presentation-mode-7.4.md)。
这些数字对应保存的验收记录，不表示每次文档修订均重新运行全量测试。

- 离线 ZIP 使用六文件白名单，CRC、包内 SHA256 与配套包外 SHA256 可复核。
- 保存的浏览器检查覆盖两场景六阶段、桌面 / 竖屏、Analysis、播放 / 暂停、Seed 42 与揭晓前信息隔离。
  在断网 Windows Chromium 中直接打开，无外部请求或页面错误。
- 两段 MP4 经 FFmpeg 完整解码：每段 600 帧、20 fps、1080×1920、30 秒；
  Chromium 实际播放到结束，无视频错误。未使用 ffprobe。
- 已检查公开内容中的敏感信息和凭据风险；检查范围有限，不保证识别所有未知凭据。

下载、录制和媒体检查说明见 [Demo 与离线包](https://github.com/zynerox/sim2clip/blob/main/docs/release-demo.md)。

## Limitations

- 当前只有 Queue 与 Server 两个注册场景，是有限模型，不是通用世界模拟器。
- 参数为明确假设；结果不代表现实商业决策、生产性能预测或统计显著性。
  Queue 不含成本收益；Server 不含网络延迟、故障、超时或重试，不保证增加节点总有收益。
- 独立验证说明模型内记录和指标一致，不能证明假设符合现实。
- 固定输入、样本、Seed 与运行版本支持模拟复现；不保证 LLM 每次计划相同，
  不承诺跨 Python / SimPy / Pydantic 版本的结果字节一致。
- 离线包不含凭据、SQLite checkpoint、私有运行记录、日志或原始模型消息，只支持保存结果回放，不能恢复 Agent 任务。
- 实测以 Windows / Python 3.12 / Chromium 为主；尚未覆盖实体手机、Safari、短视频 App 覆盖层或上传后二次转码。
  短阶段文字可在离线 HTML 中暂停阅读。

项目使用 MIT License，离线包随附 `LICENSE`。
