# Server Simulation Engine

Server 的负载生成、调度、配对比较和独立验证具有各自的模块，不调用 Queue 引擎。
模型定义见 [Server 模型与契约](server-load-balancing-m1.md)。
下文包含实现验收记录；
当前 MCP、Agent、CLI 和离线 HTML 均已接入 Server。
## 1. 文件清单

| 文件 | 职责 |
| --- | --- |
| `src/sim2clip/scenarios/__init__.py` | 不导入引擎的场景契约包 |
| `src/sim2clip/scenarios/server_contracts.py` | 八项核心契约、具体事件联合、辅助类型与跨字段约束 |
| `src/sim2clip/simulation/server_workload.py` | 独立随机流、合法样本注入、规范样本哈希 |
| `src/sim2clip/simulation/server_model.py` | 单协调进程、独立队列、RR/LO、事件、记录与指标 |
| `src/sim2clip/simulation/server_comparison.py` | 单轴设计、每 seed 共享一次负载、完整 runs/摘要/差值/排名 |
| `src/sim2clip/agent/server_validation.py` | 标准库与新契约驱动的独立证据复核 |
| `examples/server_load_balancing.py` | 单次、容量、策略 standalone JSON 导出；保存前独立验证 |
| `tests/unit/test_server_contracts.py` | 严格类型、不可变、结构/局部时间/设计约束及 JSON 往返 |
| `tests/unit/test_server_workload.py` | 已知向量、随机流独立、固定种子、抽样边界失败 |
| `tests/unit/test_server_model.py` | 手算事件表、异构速率、同刻顺序、容量/守恒/峰值/时钟 |
| `tests/unit/test_server_comparison.py` | 两比较轴、共享 tuple、逐 seed 差值、并列、LO 反例与 P95 定义 |
| `tests/agent/test_server_validation.py` | 合法形状的事件/记录/指标/来源/比较篡改拒绝 |
| `tests/integration/test_server_standalone.py` | 跨进程完整 JSON 复现、示例导出、禁止覆盖、模型依赖边界 |
| `docs/server-load-balancing-m2.md` | 本实现说明 |

## 2. 公共 API 与契约

```python
from sim2clip.scenarios.server_contracts import ServerSimulationConfig
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.agent.server_validation import validate_server_simulation

config = ServerSimulationConfig(requests=1000)
result = run_server_simulation(config)
validate_server_simulation(result, expected_config=config)
payload = result.model_dump_json()
```

还提供：

```text
run_server_comparison(request, samples_by_seed=None) -> ServerComparisonResult
validate_server_comparison(result, expected_request=None) -> None
```

单次 supplied 样本使用 `run_server_simulation(config, samples)`；每个样本必须是合法 RequestSample，数量精确匹配、ID 唯一，按 `(arrival_time, request_id)` 规范化，来源明确为 supplied。比较注入的 seed 键必须为严格 int，且恰好覆盖 request.seeds。

模型采用 frozen、extra=forbid、validate_default、revalidate_instances=always；严格标量拒绝 bool 冒充数字、字符串转数值、非有限值和越界值，JSON 数组合法转换为 tuple。`model_copy(update=...)` 不能绕过公共计算与验证入口的重新校验。

Pydantic 负责结构和局部关系，独立 Validator 负责证明计算证据：仅通过 Schema 不代表已验证实验。ServerSimulationEvent 是四种具体事件的判别联合；通过 `TypeAdapter(ServerSimulationEvent)` 验证单事件/生成 Schema。指标和事件没有退化成无类型 payload。

## 3. 调度与 M1 一致性

每个 run 创建一个 SimPy Environment 和一个协调进程。每台服务器独立 deque、active 请求和固定 rate；只有一个处理槽。协调进程选择下一真实到达/完成时刻，并显式执行：

1. 全部同刻完成，按服务器 ID 升序释放。单槽使 `(server_id, request_id)` 排序等价于服务器排序。
2. 各节点旧等待队首按服务器 ID 升序开始。
3. 新到达按请求 ID 升序，逐个 arrived → 按当前状态选择 → routed → 若节点空闲立即 started。
4. 批次全部结算后更新各节点及全局等待峰值。

每请求恰好四事件；全部 N 请求完成、队列/活动均空才成功，批次数不超过 2N。RR 游标每 run 从最小节点开始、每 routed 推进一次，不跳过繁忙节点。LO 使用 `waiting + active`，平局最小 ID，不按速率加权。

处理时长始终是 `work / processing_rate`；开始、结束及所有发布的时间遵循 M1 浮点运算。严格正但不能表示为正时长、结束与开始重合、超界或非有限值均失败，不夹紧、增加延迟或改分布。

**时钟实现细节：** 参数组合探索发现 `now + (target - now)` 的双重浮点舍入可能错过目标一个 ULP。SimPy 内部时钟通过标准库 Fraction 精确表示已计算的浮点目标，timeout 的差值也用 Fraction；输出仍是原浮点值。这不改变业务时间、事件排序或同刻定义。SimPy 4.1.2 的运行实现支持该数值加法，但其 delay 注解只写 int/float，因此该调用有一条解释明确的局部 mypy arg-type 抑制；没有放宽工程 strict 设置、替换依赖或修改 SimPy。

没有使用 Resource 回调顺序实现规则，没有调用 `queue_model.py`。独立验证器不使用 SimPy，不依赖引擎排程或指标函数。

## 4. 负载与配对来源

到达和工作量分别使用 M1 SHA-256 派生的局部 random.Random 流；生成全样本后才调度。服务器数量、速率、ID 和策略不进入抽样。batch/constant 不消耗相应流；恰好零的指数抽样最多重试 128 次，超界/非有限/到达累加不前进直接失败。

已知 seed 42、默认分布、4 请求向量：

| ID | arrival_time | work |
| --- | --- | --- |
| 0 | 0.0 | 0.08175610728494545 |
| 1 | 0.0007022752086315954 | 0.021439609079440848 |
| 2 | 0.021157706188541733 | 0.00783690344287325 |
| 3 | 0.02412599545081316 | 0.004644916160983055 |

规范 workload_hash 为 `882b6542f94d197afd74e31f39976b3741167c0bcb8b2a47f3e9fa1694d00f47`。该向量按公开 RNG 规范独立算出后固定到测试，避免生成器与验证器同时出错。

比较先生成每 seed 的一个 tuple，给全部组共享；内部计算入口保留 generated 来源，不因共享样本误标 supplied。各组运行有效 config.seed 替换为该 seed；结果顺序 seed/原组序。首组 baseline，差值 candidate−baseline；组键为稳定 group_id，同数量不同策略仍是不同组。

摘要分别记录每个指标的 seed 间 mean/min/max；P95 mean 是**各次 nearest-rank P95 的均值**。主指标为平均响应时间，精确均值相同按输入组序排名，winner 数值近似并列只按 M1 isclose 规则标记，不能解释为统计等效。

哈希使用 M1 的规范 JSON 与版本域。result_id 是配置/来源元数据标识，不是完整事件体签名，也不能代替独立验证。Agent 通过 ArtifactStore 保存完整响应摘要并核对收据，见 [多场景编排](multi-scenario-agent-m3.md)。

## 5. 手算预期与真实引擎结果

以下样本全部 supplied，完整记录和 12 事件序列由测试逐条对照 M1 表格；不是只比均值。

| 案例 | 预期平均响应 / 实际 | 预期 P95 / 实际 | 预期平均等待 / 实际 | 预期峰值 / 实际 | 预期完成 / 实际 | 预期终点 / 实际 |
| --- | --- | --- | --- | --- | --- | --- |
| A 单节点 FIFO | 3 / 3 | 4 / 4 | 1 / 1 | 1 / 1 | 3 / 3 | 6 / 6 |
| B 双节点 RR | 19/3 / 6.333333333333333 | 10 / 10 | 7/3 / 2.3333333333333335 | 1 / 1 | 3 / 3 | 11 / 11 |
| C 双节点 LO | 4 / 4 | 10 / 10 | 0 / 0 | 0 / 0 | 3 / 3 | 10 / 10 |

A 的 start/end 为 `(0,3),(3,5),(5,6)`；B 为 `(0,10),(1,2),(10,11)`，路由 1/2/1；C 为 `(0,10),(1,2),(3,4)`，路由 1/2/2。各记录 work/rate、等待、响应与节点完成数均一致。

D 异构 batch 请求 work=2、节点速率 1/2，实际 duration=2/1、平均响应=1.5、P95=2、峰值=0、终点=2，与 M1 一致。

C−B 的实际平均响应差 −7/3、P95 差 0、平均等待差 −7/3、队列峰值差 −1、终点差 −1；独立比较验证通过。另有 RR 胜 LO 的异构反例，防止预设策略赢家。

本机实际 standalone 示例（默认到达/工作假设、seed 42–46）：

| 实验 | group | 平均响应的种子均值 | 每次 P95 的种子均值 | 平均等待的种子均值 |
| --- | --- | --- | --- | --- |
| 1000 请求容量比较 | one_server | 7.797611861729327 | 14.731471465167761 | 7.7723267483612 |
| 同负载容量比较 | three_servers | 0.10793033391425369 | 0.30743777018732193 | 0.0826452205461267 |
| 1000 请求、两同速节点策略比较 | rr | 1.4903879876983468 | 3.0338492526870846 | 1.46510287433022 |
| 同负载策略比较 | lo | 1.4674586604073412 | 2.786753677552226 | 1.442173547039214 |

这些数值仅是固定模型假设下的有限实验，没有真实服务器校准、显著性检验或“LO 总更优”的结论。

## 6. 独立验证与测试

Validator 只导入新契约和标准库。它从事件重建每节点 FIFO、占用、每请求五状态人数，检查下一批次真实时刻、完成/启动/到达顺序、RR 游标、LO 当前 outstanding/平局、立即启动、生命周期及事件后计数。记录时刻必须精确匹配事件；重算 duration/work-rate、等待、响应等数值使用 M1 的绝对/ULP 容差。

从重建事件独立计算 settled 队列峰值、完成数、平均等待/响应、整数 nearest-rank P95 和总终点。generated 样本独立复现 RNG；样本哈希、来源 ID 与预期输入锁定均复核。比较再次验证每个 run、同 seed 的负载/来源、全部摘要/差值/排名/赢家及 comparison_id。

拒绝测试包括：合法形状但错误的路由、LO 平局、FIFO 全生命周期交换、同刻排序、服务重叠、事件计数、处理工作量、时间、全局/节点峰值、完成数、P95、来源哈希、seed 伪装、配对双方不同样本、摘要、差值、排名和赢家。跨进程测试改变 PYTHONHASHSEED 后要求完整 JSON 字节一致。

覆盖 10000 请求、32 节点和精确 100000 request-run 预算边界；超预算拒绝。额外 360 组 rate/mean_work/seed 参数组合经引擎执行和独立验证均成功，覆盖极端浮点时钟条件。

已保存的引擎验收为 **410 passed，0 失败 / 错误 / 跳过**，
包含 18 项 Queue Chromium 回归；Server 视觉检查见 [Server 回放](server-visualization-m4.md)。
Ruff、格式检查及 strict mypy 通过。验收环境为 Python 3.12.14、Pydantic 2.14.0、SimPy 4.1.2。

```powershell
uv run pytest tests/unit tests/integration -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## 7. Standalone 使用

在项目虚拟环境运行，不需要模型密钥，不启动 MCP/Agent：

```powershell
# 默认 seed 42–46，1 vs 3 同速服务器，完整 JSON 结果保存前独立验证
.\.venv\Scripts\python.exe examples/server_load_balancing.py --experiment capacity --requests 1000 --output outputs/server-example/capacity.json

# 两个异构节点上的 RR vs LO
.\.venv\Scripts\python.exe examples/server_load_balancing.py --experiment strategy --requests 1000 --rates 1,2 --output outputs/server-example/strategy.json

# M1 异构手算 D：单次、两请求同时到达、每请求 work=2
.\.venv\Scripts\python.exe examples/server_load_balancing.py --experiment single --requests 2 --rates 1,2 --arrival-mode batch --work-mode constant --work 2 --output outputs/server-example/heterogeneous.json
```

省略 `--output` 时输出完整 JSON 到 stdout；指定路径时用独占创建，已有文件不覆盖。capacity 示例固定 one vs three；更多合法资源配置可通过 Python API 使用 ServerComparisonRequest。示例 argparse 不是生产 CLI 的场景接入。

## 8. 已知限制

- 一个节点一个处理槽、独立无限 FIFO、速率固定、节点在线、分流与传输零耗时；无超时、失败、拒绝、重试、迁移、网络延迟和扩缩容。
- LO 只观察请求数量，异构节点下可能劣于 RR；两种策略都不保证响应时间最优。
- 实验有限且全部排空；完成数用于守恒，不能作为策略成功率。没有长期稳态、实测校准、置信区间或显著性结论。
- 极端数值可能明确失败；容差只用于重算数值，不合并近邻时刻、放宽容量或排序。时钟 Fraction 行为依赖已锁定的 SimPy 4.1.2，升级时须重跑其回归。
- 固定运行版本保证完整复现，跨 Python/SimPy/Pydantic 版本不承诺字节一致。元数据与来源 ID 是审计标识，不是不可伪造签名。
- Pydantic 校验不能代替独立证据验证，用户注入样本必须标注 supplied，不能声明为随机种子生成结果。
- Server 事件使用 oneOf/discriminator 判别联合，浏览器按具体事件标签进行契约校验；不能将 Queue 的事件处理直接套用于 Server。
