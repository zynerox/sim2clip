# Sim2Clip v1.0.0 Demo 与离线包

[GitHub Release v1.0.0](https://github.com/zynerox/sim2clip/releases/tag/v1.0.0)
提供五份公开附件；下载 ZIP 并完整解压后，可直接打开 Queue / Server HTML。
这些媒体回放已保存的真实模型实验，无需 API Key，也不会重新执行模型或模拟。

| v1.0.0 Release 下载 | 用途 |
| --- | --- |
| [sim2clip-offline-demo-v1.0.0.zip](https://github.com/zynerox/sim2clip/releases/download/v1.0.0/sim2clip-offline-demo-v1.0.0.zip) | 两场景离线 HTML 与公开来源资料 |
| [queue-presentation-v1.0.0.mp4](https://github.com/zynerox/sim2clip/releases/download/v1.0.0/queue-presentation-v1.0.0.mp4) | Queue 30 秒竖屏回放 |
| [server-presentation-v1.0.0.mp4](https://github.com/zynerox/sim2clip/releases/download/v1.0.0/server-presentation-v1.0.0.mp4) | Server 30 秒竖屏回放 |
| [SHA256SUMS.txt](https://github.com/zynerox/sim2clip/releases/download/v1.0.0/SHA256SUMS.txt) | ZIP、两段 MP4 与配套 RELEASE_NOTES.md 校验 |
| [RELEASE_NOTES.md](https://github.com/zynerox/sim2clip/releases/download/v1.0.0/RELEASE_NOTES.md) | v1.0.0 发布说明，下载版本以 Release 当前附件为准 |

正式附件以 GitHub Release 页面为准；下载 ZIP 后完整解压即可观看。
校验时使用同一来源、同一版本的 `RELEASE_NOTES.md` 与 `SHA256SUMS.txt`。
打开 `sim2clip-offline-demo-v1.0.0/queue/visualization.html` 或 `server/visualization.html`，
选择 **Presentation** 和桌面 / **9:16**，点击播放。无需 API Key、模型服务或 Web 服务；只回放保存结果。
下载文件使用配套 Release 清单校验；清单不校验自身。
包外清单校验 ZIP、两段 MP4 和发布说明，ZIP 内部清单校验包内五个有效负载文件。

## 媒体规格

| 素材 | 规格 | 大小 |
| --- | --- | ---: |
| [Queue GIF](assets/release/queue-presentation.gif) | 960×805，300 帧，10 fps，30 秒，无限循环 | 3,882,806 bytes |
| [Server 封面](assets/release/server-poster.png) | 1080×1920 PNG，展示时刻 11.5 秒 | 258,951 bytes |
| Queue MP4 | 1080×1920，H.264，20 fps，600 帧，30 秒 | 2,210,192 bytes |
| Server MP4 | 1080×1920，H.264，20 fps，600 帧，30 秒 | 1,926,627 bytes |
| Offline Demo ZIP | 两个自包含 HTML 与四个说明/校验文件 | 1,630,289 bytes |

全部媒体只包含 Presentation 舞台，没有浏览器地址栏或外部控制面板。
视频无音轨，字幕来自真实执行记录和实验说明，没有虚构实时模型思考画面。

## 来源、指标与边界

Queue / Server HTML 分别来源于 R1 A / B 的真实 ModelPlanner / Qwen3.5-Plus 执行，
不是 DemoPlanner 录制。来源应用版本为 0.1.0，发布包装版本为 1.0.0；
PROVENANCE 保留实验指标、媒体来源和文件校验信息。

Manifest 的 planner_kind=real_llm、evidence_completeness=complete；工具调用 success、
cached=false、association=verified；Agent 当时验证与 export 复核分别保留。
工具名分别为 compare_simulations、compare_server_simulations，未构造新调用。

- Queue：100 位顾客，1 / 3 个收银台，共享 FIFO；指数到达均值 10 秒、服务均值 25 秒。
  默认 Seed 42 平均等待 **745.18 / 20.59 秒**；五个配对 Seeds 42–46 均值为
  **789.52 / 33.27 秒**。模拟结束时刻分别为 2419.919860 / 952.961807 秒。
- Server：1000 个请求，1 / 3 台服务器，RR，每节点独立 FIFO、capacity=1、
  processing_rate=1 normalized work/s；指数到达均值 0.01 秒、工作量均值 0.025。
  Seed 42 平均响应 **7.1000 / 0.0876 秒**；五种子均值为 **7.797612 / 0.107930 秒**。
- 30 秒是展示时钟 p。只有 10–24 秒按原控制器映射共享模拟时钟 t，原事件数据不变。
  原任务的模型往返、工具节点跨度、模拟时间与媒体时长不能混用。
- 六阶段是保存记录的叙事：核验阶段在 A/B 事件回放之前，不表示 Validator 在引擎计算之前执行。
- 有限假设模型不代表真实商业决策、生产性能预测或统计显著性；Queue 不含成本收益，
  不保证所有 Server 负载下增加节点均有收益。

## 离线 ZIP

包内六文件白名单：

```text
sim2clip-offline-demo-v1.0.0/
├── queue/visualization.html
├── server/visualization.html
├── README-demo.txt
├── PROVENANCE.json
├── LICENSE
└── SHA256SUMS.txt
```

两份 HTML 内嵌完整结果、JS、CSS、展示 Manifest 与公开 Timeline。
公开包省略可选运行元数据；实验配置、样本、事件、指标及脚本与验收源一致。
不含 SQLite、私有 runs、原始模型消息、请求头、密钥、浏览器或编码工具。
Timeline 的 reason 是既有固定脱敏说明，不是模型原始规划文本。
PROVENANCE 保留应用版本、A/B 来源、参数、Seed、指标、工具响应 SHA 和两类核验记录。
SHA256SUMS 校验五个有效负载文件。

完整解压后用 Chromium / Chrome / Edge 打开任一 HTML。观看模式默认为 Presentation，
画面布局可选桌面或 9:16；点击播放 / 重播。Analysis 可查完整参数、事件与多种子汇总。
无需 API Key、网络、Web 服务或本机原项目路径；不会重新执行 LLM / 引擎，不能恢复 Agent 任务。

下载时同时取得配套 `SHA256SUMS.txt`，对比本机计算值：

```powershell
Get-FileHash .\sim2clip-offline-demo-v1.0.0.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

文件大小和哈希可能随公开元数据修订变化，使用与所下载附件配套的清单。

## 录制与再现

使用 Windows Chromium 155.0.8059.39、Playwright、Pillow 及 FFmpeg 7.1。
FFmpeg 可通过 imageio-ffmpeg 提供，需支持 libx264；它不随演示 ZIP 分发。
录制只消费保存结果，不请求模型或重跑实验。

以下从仓库根目录执行。`$releaseSource` 指向离线包解压后的根目录，
`$releaseBrowser` 和 `$releaseFfmpeg` 指向本机实际安装位置；输出使用新目录。

```powershell
$releaseSource = 'offline-demo/sim2clip-offline-demo-v1.0.0'
$releaseBrowser = Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'
$releaseFfmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py record --scene queue --source "$releaseSource/queue/visualization.html" --browser $releaseBrowser --layout desktop --fps 10 --output outputs/my-release/queue-desktop
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py record --scene queue --source "$releaseSource/queue/visualization.html" --browser $releaseBrowser --layout vertical --fps 20 --output outputs/my-release/queue-vertical
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py record --scene server --source "$releaseSource/server/visualization.html" --browser $releaseBrowser --layout vertical --fps 20 --output outputs/my-release/server-vertical

.\.venv\Scripts\python.exe -B scripts/capture_release_media.py encode --capture outputs/my-release/queue-desktop --ffmpeg $releaseFfmpeg --output outputs/my-release/queue-presentation.gif --width 960 --gif-fps 10
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py encode --capture outputs/my-release/queue-desktop --ffmpeg $releaseFfmpeg --output outputs/my-release/queue-presentation-desktop.mp4
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py encode --capture outputs/my-release/queue-vertical --ffmpeg $releaseFfmpeg --output outputs/my-release/queue-presentation.mp4
.\.venv\Scripts\python.exe -B scripts/capture_release_media.py encode --capture outputs/my-release/server-vertical --ffmpeg $releaseFfmpeg --output outputs/my-release/server-presentation.mp4
```

录制只调用现有 seekPresentation，逐帧固定 p，不用墙钟录屏造成卡顿或改变实验事件。
桌面只在录制页临时固定 1240×1040 与页脚位置，避免各阶段高度变化；竖屏仍为原 1080×1920。
临时增加 AI 定位、Engine / Validator 职责、记录回放和有限模型字幕；不修改源 HTML。
JPEG 中间帧质量 98；H.264 CRF 18 / slow / faststart；GIF 960 宽、192 色、固定全片调色板。
GIF 保留完整 30 秒，没有压缩阶段或改原事件时间。不同 Chromium / 字体 / 编码器版本
不承诺媒体字节一致；输入 HTML 和实验数据应来自同一包。ZIP 使用固定条目顺序和打包元数据。

上述录制命令直接消费离线 ZIP 中的 HTML，不需要原 SQLite 或私有运行目录。
只有离线包不能恢复原 Agent 任务，也不能重做远程模型验收。

## 已保存的质量检查

以下为保存的媒体与浏览器检查摘要，环境为 Windows Chromium：

- 两段公开竖屏 MP4 与一段本地桌面 MP4 全片解码，共 1500 帧；桌面 MP4 不属于 Release 附件。
  检查覆盖时长、尺寸、帧率、首帧、六阶段及结果。
  所有帧通过非黑屏 / 非空白检查，选定阶段与源帧 RGB RMS 为 1.55–2.17 / 255。
- 实际 Chromium 从头播放到 ended，duration / currentTime 均为 30 秒，无视频错误。
  GIF 用 Pillow 解码全部 300 帧，loop=0，并在浏览器观察跨越一个完整循环。
  三段视频并发浏览器检查中两段竖屏分别记录 1 / 2 帧播放掉帧，完整文件解码均为 600 帧；
  这是播放环境观察，不表示视频文件缺帧。
- 初始画面与揭晓前 DOM 没有结果卡、差值或最终 Seed 指标；实际解码图已人工查看。
  AI 计划、真实工具名、两类核验、完整 A/B、最终 Seed 42 指标均可辨认。
- 实际舞台 / Canvas 边界无越界；竖屏主要内容保留右侧 144 px 空间，页脚最下至 y=1734，
  距底边 186 px。未发现黑帧、空帧或错误裁切；固定桌面尺寸消除阶段画布跳变。
- 解压目录的两份 HTML 在 offline=true 的 Chromium 直接打开，文件校验、
  Presentation / Analysis、桌面 / 竖屏、播放 / 暂停和揭晓数字通过；外部请求与 pageerror 为 0。
- 已检查公开发布内容中的敏感信息和凭据风险，未发现已知凭据、私有字段或本机路径；检查不保证识别所有未知凭据。
  公开 ZIP 不含 .env、SQLite、私有 runs、原始模型消息、隐藏推理或供应商元数据。

MP4 用 FFmpeg 全片解码及 Chromium 实际播放交叉验证，未使用 ffprobe。
视频并发播放的少量掉帧是当时播放环境观察，不是媒体文件缺帧。

## 观看限制

验收以 Windows Chromium、桌面和 CSS 竖屏视口为主。
实体手机、Safari、抖音 App 覆盖层、上传后二次转码和普通观众理解测试尚未完成。
工具与核验阶段各 2 秒；如需仔细阅读技术说明，可在离线 HTML 中暂停播放或拖动时间轴。

发布说明见 [v1.0.0 发布说明](release-notes-v1.0.0.md)。
