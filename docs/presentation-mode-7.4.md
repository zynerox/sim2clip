# Presentation Mode：六阶段叙事与验收

Presentation 使用已保存的实验结果和执行证据呈现完整实验过程。
它与 Analysis 共用事件回放及场景模型；30 秒是展示时长，不是原任务执行耗时。
本文说明六阶段叙事和展示时钟。
以下验收记录针对已保存的 Presentation 结果。

## 使用和离线交付

普通导出仍默认 Analysis。在舞台外分别选择“观看模式”和“画面布局”，得到 Desktop /
Vertical × Analysis / Presentation 四种组合。Presentation 的播放、暂停、重播、速度、
时间轴、Seed、方案与 JSON 加载继续使用原控制栏。桌面左右排列，竖屏上下排列。

公开 Queue / Server 离线 HTML、GIF 和视频见 [Demo 与离线包](release-demo.md)。
离线包中的 Queue / Server HTML 默认使用 Presentation。

所有脚本、样式、展示 Manifest、完整结果和公开 Timeline 均内嵌 HTML。直接以 `file://`
打开可完整播放，不需要模型、CDN、在线字体或网络请求。演示使用 R1 原 A/B 保存结果，
通过只读 `mode=ro&immutable=1` checkpoint 和 SHA 收据核对真实执行记录；没有新模型请求。
开场使用已保存的真实模型 A/B 实验问题；普通结果文件按真实配置生成规范问题。
选择其他方案时重新生成当前对照问题，避免公开原问题描述上一个对照组。

## 实际架构

`presentation.py` 在 visualization 中定义不可变的 Pydantic 展示类型，
与模拟 Schema、AgentState、MCP 契约和 LangGraph 拓扑分别处理：

| 类型 | 实际保存内容 |
| --- | --- |
| PresentationManifest v1 | 场景、原结果 ID / 规范 JSON SHA、来源、配置、公开执行摘要 |
| PresentationConfig v1 | 总时长、六段时长、selected_seed 口径、默认观看模式 |
| PublicExecutionSummary v1 | 经审查的公开问题、来源、Planner 枚举、证据完整性、合法主指标、调用与核验事实 |
| PresentationState | 运行时 p / t、阶段、播放状态、布局、选中 result IDs、揭晓可见性；不写入业务结果 |

调用投影仅保留注册工具名、success/error/unknown、缓存、轮次、尝试次数、节点完成记录时间、
受核对的响应 SHA 与 result IDs。原 messages、AIMessage、reason、异常正文、请求头、
provider 元数据、私有问题和隐藏推理不进入新展示对象。配置、控制/变化变量与结果 ViewModel
直接从原类型化结果派生，不在 Manifest 中复制指标、排行榜或赢家。

`build_manifest` 先用原独立验证器复核结果。若提供 Agent state + ArtifactStore，则重新核对
响应 SHA、原工具参数和结果，确认响应与当前结果相同或包含当前单次 run，才标记关联已核实。
Planner 种类还需匹配相应轮次的真实 Planner 记录；llm、offline_demo、unknown 分别显示。
原 Agent 验证需要相同轮次、真实 result_validator 节点与 valid/sufficient 字段。
导出时复核单独保存为 origin=export，绝不冒充 Agent 当时执行的节点。

CLI 正常 run/resume 导出具备完整 state/store，可核对关联。旧独立 Timeline 或 public state
没有响应引用时保留已记录调用事实，但明确“关联尚未核实”；不根据结果类型推断曾调用工具。
无 Agent 记录显示“仅有实验结果 / Agent 当时验证：记录缺失”。工具失败、缓存、未知状态
分别如实显示。Agent valid=false 或 sufficient=false 阻止正常揭晓。

`presentation.js` 是共享六阶段控制器与舞台外壳，负责 p、模式、阶段视图和信息挂载。
两个播放器分别接线原 Seek；Queue / Server 的 Replay 核心与 ScenePose 不变。
`queue-vertical.js`、`server-vertical.js` 各自派生业务计划、问题、术语与指标格式，并复用原
精确聚合和 Canvas 绘制。没有通用无类型业务字典、第三场景、插件框架或状态管理依赖。

## 六阶段与双时钟

| 展示时钟 p | 阶段 | 画面 | 模拟时钟 t |
| --- | --- | --- | --- |
| 0 ≤ p < 3 | 问题 | 品牌、公开问题、场景与真实条件 | 0 |
| 3 ≤ p < 6 | 计划 | 真实配置、控制/变化变量、Seed、主指标 | 0 |
| 6 ≤ p < 8 | MCP 执行 | 保存的工具状态、缓存与结果关联 | 0 |
| 8 ≤ p < 10 | 独立核验 | 原 Agent 验证与导出复核分别标注 | 0 |
| 10 ≤ p < 24 | 同步回放 | 最大视觉区域、A/B、实时精确计数 | 0 → T |
| 24 ≤ p ≤ 30 | 结果揭晓 | 当前 Seed 的结果、真实差值与范围说明 | T |

六个时长仅在 PresentationConfig 中编排并检查正数与总和。控制器计算累计边界；改变配置
可使用另一总时长，测试还验证了 60 秒配置。默认 `T=max(当前选中各组结束时间)`，
`t=T×clamp((p−10)/14,0,1)`。两个 Replay 接收同一个 t，先完成的组保持真实终态。
开场和结果停留不改变模拟事件、处理时间、样本或任何实验结果。14 秒回放允许跨过极短事件，
不会人为延长生命周期。

所有阶段由当前绝对 p 派生。反向 Seek 立即卸载结果卡，重播回到 p=0/t=0；暂停冻结两时钟。
布局切换保持 p/t/Seed/方案/playing。第一次从 Analysis 中段进入，使用
`p=10+14×t/T`；t=T 进入 p=24，初始 t=0 进入问题段。同选择、同 t 的模式往返保留原 p；
Analysis 已改变 t 时重新映射。切换 Seed、方案或有效新 JSON 沿用原暂停/归零规则并清除旧 p。

原 `Sim2Clip.seek(t)` 单位仍是模拟秒。Presentation 中直接模拟 Seek 对应回放段
（t=0 → p=10，t=T → p=24）。`Sim2Clip.seekPresentation(p)` 使用展示秒，
`getPresentationState()` 返回派生状态。播放器在 t=T 后继续展示结果，直到 p=30 才暂停。

## 信息隔离和结果口径

Presentation 进入时将原 header、分析 panels、统计汇总、报告、Timeline、图例、模型说明、
Analysis 竖屏舞台和页脚从 DOM 脱离，保留对象供切回分析。当前阶段仅挂载相应公开卡；
Canvas 仅在模拟段挂载。隐藏区域没有可聚焦的报告/明细控件或 ARIA 结论残留。
控制栏终点改为“30 秒 · 演示结束”，不显示未来模拟完成时刻。
阶段切换同步替换同尺寸内容区域，无空帧或舞台布局跳变；阶段轨道强调随 p 确定。

揭晓主卡始终标“本次回放 · Seed N”。Queue 展示平均等待、队列峰值和完成时间；Server
默认展示平均响应、P95 和平均等待。合法已锁定 Server 指标优先；例如 P95 可成为主卡。
其他真实指标继续保留在 Analysis 中，不要求同一张手机卡全部堆叠。
差值直接由当前两组已验证 metrics 计算，方向 B−A；相对变化分母明确为 A。
A=0 显示“不可计算”，容差内持平不预设胜者；单组只有自身结果。
多 Seed 汇总另行标注，指引在 Analysis 复核，不冒充当前动画单次指标。

R1 当前 Seed 42 的实际揭晓（均为原始结果格式化显示）：

| 场景 | A | B | B−A |
| --- | --- | --- | --- |
| Queue 平均等待 / 秒 | 745.18 | 20.59 | −724.58 |
| Server 平均响应 / 秒 | 7.1000 | 0.0876 | −7.0123 |

这些不是五个配对 Seed 的均值，也不是生产性能测量或统计显著性结论。

## 实际关键截图

所有原生截图为 1080×1920；手机图以 360/390 CSS px、DPR=1 实际渲染，并另存真实 viewport。
两场景都查看了最终 Canvas、字体、队列聚合、提前完成状态、结果卡与手机比例。

| 阶段 | Queue | Server |
| --- | --- | --- |
| 问题 | [原始问题](assets/phase7-4/queue-question.png) | [原始问题](assets/phase7-4/server-question.png) |
| 计划 | [真实计划](assets/phase7-4/queue-plan.png) | [真实计划](assets/phase7-4/server-plan.png) |
| MCP 执行 | [真实工具记录](assets/phase7-4/queue-tool.png) | [真实工具记录](assets/phase7-4/server-tool.png) |
| 独立核验 | [双来源核验](assets/phase7-4/queue-verify.png) | [双来源核验](assets/phase7-4/server-verify.png) |
| A/B 中段 | [便利店顾客](assets/phase7-4/queue-middle.png) | [请求与独立队列](assets/phase7-4/server-middle.png) |
| 最终揭晓 | [等待差值](assets/phase7-4/queue-reveal.png) | [响应差值](assets/phase7-4/server-reveal.png) |
| 360px 舞台 | [中段](assets/phase7-4/queue-middle-360.png)、[揭晓](assets/phase7-4/queue-reveal-360.png) | [中段](assets/phase7-4/server-middle-360.png)、[揭晓](assets/phase7-4/server-reveal-360.png) |
| 390px 舞台 | [中段](assets/phase7-4/queue-middle-390.png)、[揭晓](assets/phase7-4/queue-reveal-390.png) | [中段](assets/phase7-4/server-middle-390.png)、[揭晓](assets/phase7-4/server-reveal-390.png) |
| 实际手机 viewport | [360](assets/phase7-4/queue-viewport-360.png)、[390](assets/phase7-4/queue-viewport-390.png) | [360](assets/phase7-4/server-viewport-360.png)、[390](assets/phase7-4/server-viewport-390.png) |
| 桌面回放 | [Queue](assets/phase7-4/queue-desktop.png) | [Server](assets/phase7-4/server-desktop.png) |

## 测试与交互验收

已保存的工程验收为 **756 passed，0 失败 / 错误 / 跳过**，其中 225 项可视化检查、
123 项真实 Chromium 检查。验收环境为 Windows Chromium 155.0.8059.39 / Playwright。
Ruff、格式检查、strict mypy、十个 JS 的语法检查及隔离 wheel 安装检查通过。

检查覆盖六阶段边界、完整 30 秒播放、非单调 Seek、播放与暂停、布局 / 模式 / Seed 切换，
以及无 Agent、关联未知、工具失败或缺失、缓存调用、核验失败、单组、平局、零分母和合法 P95 主指标。
还覆盖公开文本隔离、DOM / ARIA / 焦点隔离、Canvas 姿态与像素一致性及 Analysis 回归。
两场景页面仅请求本地文件，无页面错误；安装包生成的 HTML 包含完整数据和展示资源。

对自行生成的 Queue Demo 截图，可使用以下命令；先按 README 创建 `queue-demo`，输出目录需未被占用：

```powershell
uv run python tests/visualization/capture_presentation.py --result outputs/queue-demo/result.json --timeline outputs/queue-demo/agent-timeline.json --output outputs/my-presentation-queue
```

该命令消费 DemoPlanner 保存结果，不调用模型或重跑模拟。
没有原 checkpoint 和响应收据时，展示会标明缺少关联证据，不复原 R1 的完整执行关联。
R1 的已保存完整演示可直接使用 [离线包](release-demo.md)。

## 限制

手机验收是 Chromium 的 360/390 CSS px 视口，尚未覆盖实体手机、Safari、触屏或具体短视频
App 覆盖层。额外柜台/服务器继续精确聚合；节点内编号与速率为次级小字，完整细节在 Analysis。
单张 Server 揭晓卡选取主指标与两个辅助指标，全部支持指标仍可在 Analysis 复核。
工具卡最多展示最后两次真实调用并标注总调用数；完整公开记录保留在 Analysis / Manifest。
缺少受核对收据的旧 Timeline 不宣称关联成功；public state 再导出也不补造原验证数据。
观看隐藏只是叙事编排，HTML 仍含完整结果；不是保密机制。

工程验收确认 30 秒内完整呈现问题、AI 计划、真实 MCP、独立核验、过程与当前 Seed 结果；
尚未对普通用户开展理解程度研究，不将自动化验收称为人群可用性测试。
