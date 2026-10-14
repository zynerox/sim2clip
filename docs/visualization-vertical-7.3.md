# Queue / Server：9:16 实验舞台

1080×1920 逻辑舞台为两场景提供独立竖屏构图，与桌面布局共用回放数据及控制器。
本文说明舞台构图、信息区域与已保存的布局检查。
六阶段叙事及结果揭晓见 [Presentation Mode](presentation-mode-7.4.md)。

## 观看与布局

公开 Queue / Server 离线演示见 [Demo 与离线包](release-demo.md)。
打开导出的 HTML，在舞台外选择 **画面布局 → 9:16 竖屏舞台**。默认桌面分析保留原 A/B
左右双栏、最终指标、技术参数、报告和真实 Agent Timeline。两种布局的播放、暂停、速度、
时间轴、反向 Seek、重播、种子、方案及 JSON 文件加载共用原播放器。

**布局切换不调用 choose/loadEvidence/seek，不重置时间、种子、实验组或播放状态。**
种子、方案和文件加载沿用原来的重新开始规则；这与单纯布局切换独立。

## 舞台架构

```text
已验证的完整结果 → 各自 Replay → 共享绝对模拟时刻 t
  Queue：SceneTimeline(桌面设计) → 原桌面 Canvas
         SceneTimeline(竖屏设计) → Queue 竖屏 Canvas
  Server：M4 ServerScenePose → 原桌面 Canvas
                           → 确定性几何重排 → Server 竖屏 Canvas
  两场景的竖屏 Canvas → 共享 Stage DOM（问题、A/B、计数、时钟、进度、图例）
```

`vertical-stage.js` 只负责公共外壳、布局切换、等比预览和信息区域。
`queue-vertical.js` 与 `server-vertical.js` 分别负责业务场景的构图、绘制与精确聚合，
没有统一两种业务状态机。`html.py` 内嵌 CSS/JS，仍可直接 file:// 离线使用，
不依赖 CDN、在线字体、框架或 fetch。

`#vertical-stage` 固定 **1080×1920**，包含两个上下排列的场景；单组时使用同一舞台中的
一个加高场景。`#vertical-preview` 通过 ResizeObserver 等比预览，桌面默认宽 540px，
手机使用可用宽度。截图脚本把预览临时设为 1080px，真实 Chromium 截取舞台根，得到
1080×1920 PNG；视频录制可以使用同一舞台 DOM/Canvas，编码由独立发布工具负责。

内容左边距 60、上方 72、右侧 144；信息末端在 y=1730 以内，底部至少保留 190 逻辑像素。
加载、播放、种子、速度与时间轴都在舞台外，不进入舞台截图。

问题由当前选中结果生成，使用真实顾客/请求数、收银台/服务器数量和对比类型。
Server 同数量但不同速率的池比较明确称“不同处理速率”；只改变服务器 ID 的合法池比较称
“不同服务器池”，不虚构速率差异。RR/LO 使用策略问题。
单组不构造 B 组或赢家。标题按实际内容适配字号，没有固定官方 Demo 问题。

舞台底部的 `data-result-slot="reserved"` 为结果区域提供挂载位置。
Presentation 控制结果揭晓；Analysis 显示完整指标。观看模式与画面布局分别选择。

## Queue 构图与守恒

复用 Queue SceneTimeline、真实事件关键帧、绝对时刻插值和碰撞聚合。
仅为 `cashierPosition` 增加竖屏设计分支；桌面设计、Python 参考和 GIF 绘制保持原样。
人物由原几何角色放大 1.38 倍，不使用 Emoji 或新的随机动画。

A/B 等待区是 5×3 蛇形槽位，单组为 5×5。最多显示 6 个真实收银台；单台用加大的柜台，
三台沿服务区排列，更多柜台使用两列。超出容量、移动遮挡和离场都按真实 ID 聚合，
“另有 N 人等待”和“另 N 台 / 忙碌 N”明确列出。出口标识移到离场路径旁，避免遮住人物。

每个 t 保持：

- 未到达 + 等待 + 结账 + 完成 = config.customers。
- 可见等待 + 等待聚合 = Replay FIFO 长度。
- 可见服务 + 服务聚合 = Replay 忙碌收银台数。
- 可见离场 + 完成聚合 = Replay 完成数。

较早完成的组保持终态，仍使用另一组的绝对时钟，不各自归一化或延长实验终点。

## Server 构图与守恒

保留 M4 Replay、路由决策和 ScenePose。适配层只重排原姿态坐标；请求仍来自该姿态的
真实 request_id/server_id/status。入口 → LB → 独立 FIFO → 处理槽 → 完成由连接线串起。
单台居中且使用较大节点，三台上下分支，每台显示编号、真实 work/s 速率和等待数量。
最近实际路由以绿色分支强调，不改变模型的零网络耗时。

每个可见服务器最多绘制 3 个真实等待代表，其他等待用“另 N”计数。并发路径重叠时用
×N 表示真实请求数量。聚合按实际距离判断，避免网格分界两侧的近邻块相互遮挡；
token_groups 保留每个成员的真实 ID、服务器、状态和位置。最多显示 3 个节点；更多节点以准确的隐藏节点数量、等待、处理和
完成总数表示，完整节点仍可在桌面分析区滚动查看。没有删除或重路由请求。

每个 t 保持：未到达 + 等待 + 处理 + 完成 = config.requests。
等待实体数 + 等待聚合 = 精确等待数；可见节点与隐藏节点队列之和也等于精确等待数。
处理实体所属服务器与 Replay active、等待实体与 Replay FIFO 一致。

超过三位的编号不挤进小角色身体；完整身份仍保留在姿态、原结果及分析信息中。
处理槽长编号与长速率自动适配文字宽度，大时间用科学计数法显示秒数，只改变显示格式。

## 真实截图与数值

主要舞台复用 R1 A/B 的已保存结果与 Agent Timeline，单组复用 R1.1 G/H。
没有重新调用远程模型、MCP 或改写历史结果。补充 RR/LO、异构与 Server 大规模场景复用 M4
保存结果；Queue 压力图使用独立测试夹具（10000 人同时到达、服务 10 秒、100 台），
明确与真实 LLM 案例区分。

| 场景 | 初始 | 高峰完整 1080×1920 | 中段完整 1080×1920 | 结束完整 1080×1920 | 手机 360px | 手机 390px |
| --- | --- | --- | --- | --- | --- | --- |
| Queue | [初始](assets/phase7-3/queue-initial-1080.png) | [排队高峰](assets/phase7-3/queue-peak-1080.png) | [中段](assets/phase7-3/queue-middle-1080.png) | [结束](assets/phase7-3/queue-end-1080.png) | [360](assets/phase7-3/queue-mobile-360.png) | [390](assets/phase7-3/queue-mobile-390.png) |
| Server | [初始](assets/phase7-3/server-initial-1080.png) | [队列积压](assets/phase7-3/server-peak-1080.png) | [中段](assets/phase7-3/server-middle-1080.png) | [结束](assets/phase7-3/server-end-1080.png) | [360](assets/phase7-3/server-mobile-360.png) | [390](assets/phase7-3/server-mobile-390.png) |

| 截图时刻 | A：等待 / 服务或处理 / 完成 | B：等待 / 服务或处理 / 完成 |
| --- | --- | --- |
| Queue 高峰 922.169929 秒 | 64 / 1 / 35 | 1 / 3 / 96 |
| Queue 中段 1209.959930 秒 | 53 / 1 / 46 | 0 / 0 / 100 |
| Queue 结束 2419.919860 秒 | 0 / 0 / 100 | 0 / 0 / 100 |
| Server 高峰 10.226369 秒 | 586 / 1 / 413 | 16 / 3 / 981 |
| Server 中段 12.431055 秒 | 495 / 1 / 504 | 0 / 0 / 1000 |
| Server 结束 24.862110 秒 | 0 / 0 / 1000 | 0 / 0 / 1000 |

补充截图：

- [RR/LO 真实分流差异，t=3.12](assets/phase7-3/rr-lo.png)：RR 的 #2 等待 S1，LO 的 #2 在 S2 处理。
- [异构速率 1/2](assets/phase7-3/heterogeneous.png)。
- [R1.1 Queue 单组](assets/phase7-3/queue-single.png)、[R1.1 Server 单组](assets/phase7-3/server-single.png)。
- [10000 顾客 / 100 台压力夹具](assets/phase7-3/queue-extreme.png)：9900 等待、100 结账。
- [M4 10000 请求聚合](assets/phase7-3/server-extreme.png)：9997 等待、3 处理；9 个等待实体 + 9988 聚合。
- [早期路由聚合边界，t=0.0015](assets/phase7-3/server-route-boundary.png)：A 的等待/处理近邻合并为 ×2，B 的两处理请求也精确表示为 ×2。

截图检查覆盖原始 1080 图和 360 / 390 像素预览的可读性、人物与节点识别、
文字溢出、路径标签遮挡和关键对象裁切。完成状态的空队列来自实际事件终态。
表中的图片为已保存实验截图；压力测试夹具与真实模型案例分别标注。

## 浏览器交互与回归

真实 Windows Chromium **155.0.8059.39** / Playwright，DPR=1，25 项竖屏浏览器测试涵盖：

- 1080×1920 逻辑舞台、360/390px 实际 CSS 视口、无舞台控制元素与平台安全区。
- 播放、暂停、2×、时间轴拖动、反向 Seek、非单调 Seek、连续播放后同一 t 的姿态/像素一致。
- 桌面 ↔ 竖屏保留全部 Replay 数据、选择、时间与 playing 状态。
- 不同 seed、第三方案、有效 JSON 文件加载、单组标题和布局更新。
- 两场景事件状态、实体身份、服务器归属、人数与精确聚合守恒。
- Queue 1/3/8/100 柜台及 10000 顾客；Server 1/3/32 节点及 10000 请求。
- RR/LO、非连续 ID、异构真实速率、长 ID 和大时间的显示宽度。

已保存的竖屏版本工程验收为 **705 passed，0 失败 / 错误 / 跳过**，
包括 174 项可视化检查，其中 87 项使用真实 Chromium。
Ruff、格式检查、strict mypy、九个 JS 语法检查和隔离 wheel 检查通过。
安装后导出的两场景 HTML 在 Chromium 390px 下支持离线加载、Seek 和桌面 / 竖屏切换，无页面错误。

上述截图包含真实模型保存结果及单独标注的测试夹具。
若需对自己的结果生成截图，可运行 `tests/visualization/capture_vertical.py`，
为 `--result` 指定保存结果，为 `--output` 指定未被占用的目录。该脚本不执行模型或模拟。
回放高峰只在同刻事件全部结算后取值，不使用瞬时到达 / 开始之间的暂态。

## 限制

- 360px 下主问题约 17px，关键人数约 19px，人物约 21px 高；节点内 ID、速率和技术细节
  是次级信息，字体仍较小。完整长编号和全部节点请查看舞台外分析区。
- 竖屏同帧最多显示 6 个柜台 / 3 个服务器，额外节点精确聚合；未新增分页。
- Queue 已完成离场与快速服务仍遵循原窗口；极短事件可跨帧，不能为看清动作延长真实处理时间。
- 网络与店内路径是示意；移动中的角色已经处于当前真实状态，不表示新增传输/步行耗时。
- 安全区是固定预留，尚未在具体短视频平台 App 的覆盖层或实体手机上验收；布局实测为 Chromium
  360/390 CSS px 视口，不宣称 Safari、触屏操作、Linux/macOS 或物理设备验收。
- Queue 竖屏轨迹及 Server 几何重排属于浏览器展示层；Python ScenePose/GIF 使用桌面设计。
  普通 Queue GIF 继续可用，Server GIF 返回 unsupported；发布 MP4 由独立录制工具生成。
- 叙事播放与结果揭晓由 [Presentation Mode](presentation-mode-7.4.md) 提供；发布 MP4 由独立录制工具生成。
