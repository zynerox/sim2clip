# Server Load Balancing：模型与契约设计

Server 使用每节点独立 FIFO 队列，与 Queue 的共享 FIFO 模型分别计算和验证，
通过 Scenario Registry 复用 LangGraph 编排。
本文定义参数、调度顺序、事件、统计口径和手算案例。
实际类型及计算实现见 [Server Engine](server-load-balancing-m2.md)，
工具接入见 [多场景编排](multi-scenario-agent-m3.md)。

## 1. 模型职责

引擎计算请求生命周期、队列和指标；独立验证器从样本和事件复核结果。
路由与处理规则由类型契约约束，Planner 负责选择实验参数并披露默认假设。
## 2. 模型定义与区别

场景 ID 固定为 `server_load_balancing`，模型类型为 `server_queue_network`。时间为秒，工作量为归一化工作单位 `normalized_work`，速率为工作单位/秒。

一个有限请求流先经过无耗时分流器。每个请求被分配到一台服务器后，留在该服务器的 FIFO 等待队列，直到在该服务器处理完成。M1 每台服务器只有一个处理槽；不同服务器可并行处理。服务器在整个实验中保持在线、速率固定，队列没有业务容量上限。

请求处理时长由请求工作量与所选服务器速率决定。分流器只能观察当前请求数状态，不知道请求剩余工作量，也不模拟网络传输、操作系统线程或真实 CPU 共享。

与便利店共享 FIFO 的关键差异：

| 机制 | 便利店 Queue v1 | 服务器 v1 |
| --- | --- | --- |
| 等待结构 | 单一共享 FIFO | 每台服务器独立 FIFO |
| 资源选择 | 共享资源授予空闲收银台 | 到达时先分流，选定节点后不迁移 |
| 处理时长 | 样本内预生成 service_time | 样本内 work / 所选服务器 processing_rate |
| 全局顺序 | 全局 FIFO | 只保证各服务器内部 FIFO |
| 空闲条件 | 全局有队列时所有收银台应忙碌 | 某台有队列时，另一台可以空闲 |
| 比较变量 | 收银台数量 | 分流策略或服务器集合 |

不得调用 Queue 引擎再更换字段或事件名称来实现本场景。共享哈希、序列化等基础方法可以遵循相同规则，计算模型和独立验证必须分别实现。

## 3. 参数、默认值与边界

下面的上界是首版工程范围，不是现实系统的自然上限。边界包含端点，除非标为严格大于。

| 参数/数据 | 默认值 | 范围与语义 |
| --- | --- | --- |
| requests | 1000 | 严格整数，1–10000 |
| servers | 一台：ID 1、速率 1 | 1–32 台；ID 唯一并按升序提供 |
| server_id | 必填 | 严格整数，1–2^31−1；不要求连续 |
| capacity | 1 | 严格整数且只能为 1；不接受 True |
| processing_rate | 1.0 | 有限数，0.001–1000000 工作单位/秒 |
| routing_strategy | round_robin | round_robin / least_outstanding |
| seed | 42 | 严格整数，0–2^32−1 |
| arrivals.mode | exponential | exponential / batch |
| exponential.mean_interarrival_time | 0.01 | 有限数，0.001–10000 秒；首个请求固定在 0 秒 |
| batch.arrival_time | 0.0 | 有限数，0–1000000000 秒；全部请求同时到达 |
| workload.mode | exponential | exponential / constant |
| exponential.mean_work | 0.025 | 有限数，0.001–10000 工作单位 |
| constant.work | 0.025 | 有限数，严格大于 0、不超过 1000000 工作单位 |
| RequestSample.request_id | 生成时 0..N−1 | 严格整数，0–2^31−1；注入样本可不连续 |
| RequestSample.arrival_time | 采样或注入 | 有限数，0–1000000000 秒 |
| RequestSample.work | 采样或注入 | 有限数，严格大于 0、不超过 1000000 工作单位 |
| comparison_axis | 必填 | routing_strategy / server_pool |
| comparison.experiments | 必填 | 2–8 组，group_id 唯一，配置不能重复 |
| group_id | 必填 | 1–32 个字符，正则 `^[a-z][a-z0-9_-]{0,31}$` |
| comparison.seeds | 42–46 | 1–10 个唯一种子，按升序提供 |
| 单次业务事件数 | 无自由配置 | 恰好 4 × requests，最多 40000 |
| 比较工作预算 | 无自由配置 | requests × groups × seeds ≤ 100000 request-runs |
| 比较业务事件预算 | 无自由配置 | 最多 400000；完整结果最多 80 个 run |

默认 0.01 秒到达间隔、0.025 工作单位和速率 1 只是用于可观察负载的假设，绝不是实测服务器性能。自然语言“1000 个请求进入系统”没有自动等同于“1000 个同时到达”；Planner 必须披露选择的到达假设。用户明确“同时到达”时应选择 batch。

采用判别类型分开 exponential/batch、exponential/constant，避免保留不生效的均值字段。例如 batch 携带 mean_interarrival_time、constant 携带 mean_work 都应被拒绝。

## 4. 请求样本与复现

### 4.1 生成规则

每次运行先生成整个不可变请求样本，再开始任何分流与处理。

1. 生成请求 ID 0..N−1。
2. exponential 到达：a_0 = 0；a_i = a_(i−1) + gap_i；gap_i = arrival_rng.expovariate(1.0) × mean_interarrival_time。
3. batch 到达：所有 a_i = arrivals.arrival_time。
4. exponential 工作量：w_i = work_rng.expovariate(1.0) × mean_work；constant 工作量：所有 w_i = workload.work。
5. 两个局部随机数流分别使用以下规则，不使用全局 random、Python hash() 或服务器调度顺序：

```text
stream_seed = int.from_bytes(
    SHA256(ASCII("sim2clip-server-workload-v1:{seed}:{name}")),
    byteorder="big"
)
name = "arrival" 或 "work"
rng = random.Random(stream_seed)
```

arrival 和 work 流独立。更改服务器数量、ID、速率或策略不能影响任何样本。constant/batch 对应随机流不消耗随机数，不改变另一条流。

指数抽样因浮点/RNG 产生恰好 0 时可以重新抽取，最多 128 次；仍为 0 时明确失败，不无限循环。有限性、样本上界或浮点表示检查失败时直接失败，不能截尾、夹紧、四舍五入或重新抽取超界值来伪装符合原分布。指数到达累加后必须严格大于上一到达时刻；浮点加法吞掉正间隔时失败，不能将它静默解释成 batch。首版指数模式没有另设截断分布。

注入样本必须提供精确 N 个、ID 唯一、字段合法的 RequestSample，并按 (arrival_time, request_id) 规范排序；不得重新生成、补齐或重编号。样本来源保存为 supplied，配置中的采样设置仅作来源记录，不声称注入样本服从该分布。generated 来源才按上述采样规则复现。

### 4.2 配对比较

- 每个 seed 只生成一份完整样本，同一个不可变 tuple 提供给该 seed 下全部组。
- 共享的是到达时间和工作量，不是实际 processing_time；异构节点和不同分流可能改变处理时长。
- 不同 seed 使用各自独立的样本。在一个 seed 内做配对差值，再汇总各组 run 指标。
- ServerComparisonRequest.seeds 是比较的种子依据。所有组配置的 seed 必须等于 seeds[0]；不接受冲突后静默忽略。执行每个 run 时，将配置中的 seed 明确替换为当前 seed，并保存有效配置。
- comparison_axis=routing_strategy：服务器集合及全部负载设置必须相同，只能改变策略。因为首版只有两种策略，此轴实际只有两组。
- comparison_axis=server_pool：策略及全部负载设置必须相同，只能改变服务器集合，允许数量或处理速率不同。若同时改变数量与速率，报告必须描述整个资源配置变化，不能解释成单独数量的因果效果。
- 同时改变策略和服务器集合的比较请求被拒绝。一次实验不能混入便利店配置。
- 首组为 baseline；group_id 标识方案，不能以服务器数量作为分组键。

模型层允许单个 seed，以支持手算及试验。Agent 的随机实验证据策略默认至少五个配对 seed；batch+constant 的确定性实验无需为满足形式重复五次。最低种子数不是显著性保证。

## 5. 策略、事件生命周期与同刻调度

### 5.1 Round Robin

服务器顺序是 server_id 升序，不是进程创建顺序。

- 每次 run 的游标初始化为索引 0，即最小 server_id。
- 每个 request_routed 使用当前位置的服务器，然后 cursor = (cursor + 1) % server_count。
- 所有请求都推进一次游标；节点忙碌时仍将请求放入该节点队列。
- 完成事件、处理开始、到达事件本身不推进游标；不跳过忙碌或慢速节点。
- 不同 seed、不同组以及重复独立运行都从索引 0 重新开始。

### 5.2 Least Outstanding

在当前请求入队之前，读取各服务器：

```text
outstanding(server) = len(waiting_queue(server)) + active_count(server)
selected = min(servers, key=(outstanding, server_id))
active_count ∈ {0, 1}
```

- 不包含尚未到达、正在分流、已完成请求。
- 先前同刻请求已经完成分流/立即开始处理，其状态参与下一请求选择。
- 平局选最小 server_id；不轮换平局，不随机选节点。
- 不按速率加权，不看当前请求工作量，不预知剩余处理时间。它是请求数均衡策略，不是预计完成时间最短策略。

### 5.3 生命周期

每个请求恰好四个事件：

```text
not_arrived
  → request_arrived → dispatching
  → request_routed → queued
  → request_started → processing
  → request_completed → completed
```

arrival 和 routed 同刻；route 和 started 可以同刻。dispatching、零等待 queued 是审计序列中的瞬时状态，不产生模拟延迟。没有 rejected、timed_out、retry 或 cancelled 状态。

request_routed 将请求追加到所选服务器队尾。request_started 只能移除该服务器队首，且该服务器必须空闲。request_completed 必须对应该服务器当前活动请求，然后释放处理槽。

### 5.4 同一时间戳的规范顺序

对每个精确浮点时刻 t，按以下顺序结算：

1. **完成批次**：处理全部 processing_end_time == t 的请求，按 (server_id, request_id) 排序，各自产生 request_completed。
2. **旧队列启动批次**：对空闲且已有等待请求的服务器，按 server_id 升序启动队首，各自产生 request_started。
3. **新到达批次**：对 arrival_time == t 的新请求，按 request_id 升序，逐个完成下列完整步骤后才处理下一个请求：
   - request_arrived；
   - 根据已经更新的服务器状态选节点，入队并产生 request_routed；
   - 若该节点空闲，立即启动其队首并产生 request_started。
4. 同刻批次完成后，才计算该时刻的展示快照和队列峰值。

所有服务时长严格为正且 scheduled_end > start，因此本批次新启动请求不能产生同刻完成。不得按 event_type 对保存结果再次整体排序；例如新到达阶段的 started 必须保留在下一请求 arrived 之前。

独立的同刻完成先全部释放；旧等待先于同刻新到达开始处理；新请求逐个选择并更新状态。以上是服务器 v1 的模型规则，不覆盖 Queue v1 的原有顺序。

### 5.5 时间换算与结束条件

对请求 i 及选中服务器 s：

```text
processing_time_i = work_i / processing_rate_s
start_i = 该服务器实际取出队首的时刻
end_i = start_i + processing_time_i
wait_i = start_i − arrival_i
response_i = end_i − arrival_i
dispatch_i = arrival_i
```

不抢占、不共享该处理槽的速率、不迁移请求。节点空闲且队列非空时必须在该时刻启动；其他节点空闲不要求迁移本节点等待请求。

只有 N 个请求全部 completed，全部队列为空、全部 active 为空，才返回成功。若还有等待请求但没有未来到达/完成事件，必须作为内部死锁失败。不能依靠任意时长截断输出部分成功结果。

设 A=max(arrival_time)，W=sum(work)，r_min=min(processing_rate)，则有效有限输入的数学完成上界为：

```text
T_end ≤ A + W / r_min
       ≤ 1000000000 + 10000 × 1000000 / 0.001
       = 10001000000000 秒
```

这个很宽的上界仅用于数值/契约边界，不是默认实验时长。业务事件恰好 4N；协调时刻至多包含 N 个到达和 N 个完成，共至多 2N 个不同批次。

M2 建议使用一个 SimPy Environment 和单个协调进程推进下一到达/完成时刻，维护每台服务器的 deque、active 和待完成时间索引；在 SimPy 时钟上显式执行上述批次规则。不得依赖多个 Resource 回调恰好按某种创建顺序触发来实现本规范，也不能调用 Queue 引擎。实现方式可优化，但上述事件序列必须不变。

## 6. 队列与指标的精确定义

### 6.1 事件后状态

- server_queue_length_after_event：关联服务器中已经 routed、尚未 started 的请求数；不包含 active。
- total_queue_length_after_event：所有服务器等待数之和；不包含 dispatching、active 或 completed。
- server_active_after_event：关联服务器活动请求数，0 或 1。
- arrived 事件的 server_id、server_queue_length_after_event、server_active_after_event 必须是显式 null；总等待数仍有真实值。
- routed 事件入队后计数，started 出队并占用后计数，completed 释放后计数。

不要为每个事件复制全部服务器状态。独立验证器和回放器可用事件重建全部队列、活动请求和分流选择；每个事件只带关联节点及总计数。

### 6.2 指标

所有指标仅描述有限实验。无稳态、置信区间或显著性结论。

| 字段 | 定义 |
| --- | --- |
| average_response_time | fmean(end_i − arrival_i)，包含等待和处理 |
| p95_response_time | 响应时间升序 x；nearest-rank：x[ceil(0.95N)−1]，不插值 |
| average_wait_time | fmean(start_i − arrival_i) |
| max_queue_length | 每个完整同刻批次后的总等待数最大值，初始空状态计入 |
| completed_requests | completed 事件数，成功结果必须等于 N |
| simulation_end_time | max(end_i)，从模拟时刻 0 起算；不是最后到达后耗时 |
| per_server[].completed_requests | 该服务器完成请求数，可为 0 |
| per_server[].max_queue_length | 该服务器在完整同刻批次后的等待峰值 |

总队列峰值一般不等于各服务器峰值之和。不能取任意单事件中的瞬时峰值作为指标；同刻立即服务的请求不会构成真实等待峰值。P95 在小样本中可能等于最大值；三个请求的 P95 就是最大响应时间。

多种子汇总分别保存每个指标的种子间 mean/minimum/maximum。p95_response_time.mean 是“每次实验 P95 的均值”，不是把所有请求合并后的 P95。baseline 与 candidate 的差值方向统一为 candidate − baseline；负响应时间差表示候选更低。

首版比较主指标固定为 average_response_time。ranking 按该指标的组均值升序，精确均值相同时按请求中的组顺序稳定排序。winner 包含与最小均值满足 isclose(rel_tol=1e−9, abs_tol=1e−9) 的全部组，标签为数值近似并列，不能解释为统计等效。

## 7. 独立 Pydantic Schema 设计

契约定义于 `src/sim2clip/scenarios/server_contracts.py`，不继承 Queue v1 业务类型；契约模块不导入引擎、Agent 运行时或可视化。

使用标量严格约束、extra=forbid、frozen、validate_default、拒绝 NaN/Infinity 和 revalidate_instances。允许合法 JSON 数组转换成内部 tuple；不允许字符串转数字、bool 充当整数或字段拼写错误。JSON number 中整数数值可以作为浮点字段输入。这与 MCP 将 JSON 解码为 dict/list 的方式兼容；不能仅设全局 strict=True 后忽略 tuple 的 Python/JSON 入口差异。

以下 Python 块用于说明字段与类型设计；第 8 节规定跨字段和跨记录验证规则。
实际类型以 `src/sim2clip/scenarios/server_contracts.py` 为准，验证器见 Server Engine 文档。

<!-- M1_SCHEMA_START -->
```python
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

RequestCount = Annotated[int, Field(strict=True, ge=1, le=10000)]
Count = Annotated[int, Field(strict=True, ge=0, le=10000)]
RequestId = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
ServerId = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
SequenceNumber = Annotated[int, Field(strict=True, ge=0, le=39999)]
SingleCapacity = Annotated[int, Field(strict=True, ge=1, le=1)]
ActiveCount = Annotated[int, Field(strict=True, ge=0, le=1)]
ActiveOne = Annotated[int, Field(strict=True, ge=1, le=1)]
ActiveZero = Annotated[int, Field(strict=True, ge=0, le=0)]
ArrivalTime = Annotated[float, Field(strict=True, ge=0, le=1_000_000_000, allow_inf_nan=False)]
Time = Annotated[float, Field(strict=True, ge=0, le=10_001_000_000_000, allow_inf_nan=False)]
PositiveTime = Annotated[
    float, Field(strict=True, gt=0, le=10_001_000_000_000, allow_inf_nan=False)
]
ProcessingTime = Annotated[float, Field(strict=True, gt=0, le=1_000_000_000, allow_inf_nan=False)]
MeanArrival = Annotated[float, Field(strict=True, ge=0.001, le=10000, allow_inf_nan=False)]
MeanWork = Annotated[float, Field(strict=True, ge=0.001, le=10000, allow_inf_nan=False)]
Work = Annotated[float, Field(strict=True, gt=0, le=1_000_000, allow_inf_nan=False)]
Rate = Annotated[float, Field(strict=True, ge=0.001, le=1_000_000, allow_inf_nan=False)]
SignedTime = Annotated[
    float,
    Field(strict=True, ge=-10_001_000_000_000, le=10_001_000_000_000, allow_inf_nan=False),
]
SignedCount = Annotated[int, Field(strict=True, ge=-10000, le=10000)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
GroupId = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
VersionString = Annotated[str, Field(strict=True, min_length=1, max_length=64)]
RoutingStrategy = Literal["round_robin", "least_outstanding"]


class ServerContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class ServerConfig(ServerContractModel):
    server_id: ServerId
    processing_rate: Rate = 1.0
    capacity: SingleCapacity = 1


class ExponentialArrivals(ServerContractModel):
    mode: Literal["exponential"] = "exponential"
    mean_interarrival_time: MeanArrival = 0.01


class BatchArrivals(ServerContractModel):
    mode: Literal["batch"] = "batch"
    arrival_time: ArrivalTime = 0.0


ArrivalConfig = Annotated[ExponentialArrivals | BatchArrivals, Field(discriminator="mode")]


class ExponentialWork(ServerContractModel):
    mode: Literal["exponential"] = "exponential"
    mean_work: MeanWork = 0.025


class ConstantWork(ServerContractModel):
    mode: Literal["constant"] = "constant"
    work: Work = 0.025


WorkConfig = Annotated[ExponentialWork | ConstantWork, Field(discriminator="mode")]


class ServerSimulationConfig(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    simulation_type: Literal["server_queue_network"] = "server_queue_network"
    requests: RequestCount = 1000
    servers: Annotated[tuple[ServerConfig, ...], Field(min_length=1, max_length=32)] = (
        ServerConfig(server_id=1),
    )
    routing_strategy: RoutingStrategy = "round_robin"
    seed: Seed = 42
    arrivals: ArrivalConfig = Field(default_factory=ExponentialArrivals)
    workload: WorkConfig = Field(default_factory=ExponentialWork)
    time_unit: Literal["second"] = "second"
    work_unit: Literal["normalized_work"] = "normalized_work"
    queue_discipline: Literal["fifo"] = "fifo"
    scheduling_policy: Literal["complete_then_dispatch"] = "complete_then_dispatch"


class ServerExperiment(ServerContractModel):
    group_id: GroupId
    config: ServerSimulationConfig


class ServerComparisonRequest(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    comparison_axis: Literal["routing_strategy", "server_pool"]
    experiments: Annotated[tuple[ServerExperiment, ...], Field(min_length=2, max_length=8)]
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=10)] = (42, 43, 44, 45, 46)


class RequestSample(ServerContractModel):
    request_id: RequestId
    arrival_time: ArrivalTime
    work: Work


class RequestRecord(ServerContractModel):
    request_id: RequestId
    server_id: ServerId
    arrival_time: ArrivalTime
    work: Work
    dispatch_time: ArrivalTime
    processing_start_time: Time
    processing_end_time: PositiveTime
    processing_time: ProcessingTime
    wait_time: Time
    response_time: PositiveTime


class ServerEventBase(ServerContractModel):
    sequence: SequenceNumber
    timestamp: Time
    request_id: RequestId
    total_queue_length_after_event: Count


class RequestArrivedEvent(ServerEventBase):
    event_type: Literal["request_arrived"]
    server_id: None
    server_queue_length_after_event: None
    server_active_after_event: None


class ServerBoundEvent(ServerEventBase):
    server_id: ServerId
    server_queue_length_after_event: Count


class RequestRoutedEvent(ServerBoundEvent):
    event_type: Literal["request_routed"]
    server_active_after_event: ActiveCount


class RequestStartedEvent(ServerBoundEvent):
    event_type: Literal["request_started"]
    server_active_after_event: ActiveOne


class RequestCompletedEvent(ServerBoundEvent):
    event_type: Literal["request_completed"]
    server_active_after_event: ActiveZero


ServerSimulationEvent = Annotated[
    RequestArrivedEvent | RequestRoutedEvent | RequestStartedEvent | RequestCompletedEvent,
    Field(discriminator="event_type"),
]


class PerServerMetrics(ServerContractModel):
    server_id: ServerId
    completed_requests: Count
    max_queue_length: Count


class ServerMetrics(ServerContractModel):
    average_response_time: PositiveTime
    p95_response_time: PositiveTime
    average_wait_time: Time
    max_queue_length: Count
    completed_requests: RequestCount
    simulation_end_time: PositiveTime
    per_server: Annotated[tuple[PerServerMetrics, ...], Field(min_length=1, max_length=32)]


class ServerEngineMetadata(ServerContractModel):
    server_model_version: Literal["server-lb-1"] = "server-lb-1"
    workload_version: Literal["server-workload-1"] = "server-workload-1"
    routing_rules_version: Literal["server-routing-1"] = "server-routing-1"
    schedule_version: Literal["server-schedule-1"] = "server-schedule-1"
    hash_version: Literal["sha256-json-v1"] = "sha256-json-v1"
    python_version: VersionString
    simpy_version: VersionString
    pydantic_version: VersionString


class ServerSimulationResult(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    result_kind: Literal["single"] = "single"
    result_id: Digest
    config: ServerSimulationConfig
    metadata: ServerEngineMetadata
    workload_source: Literal["generated", "supplied"]
    workload_hash: Digest
    request_samples: Annotated[tuple[RequestSample, ...], Field(min_length=1, max_length=10000)]
    request_records: Annotated[tuple[RequestRecord, ...], Field(min_length=1, max_length=10000)]
    metrics: ServerMetrics
    events: Annotated[tuple[ServerSimulationEvent, ...], Field(min_length=4, max_length=40000)]


class ServerMetricSummary(ServerContractModel):
    mean: Time
    minimum: Time
    maximum: Time


class ServerExperimentSummary(ServerContractModel):
    group_id: GroupId
    result_ids: Annotated[tuple[Digest, ...], Field(min_length=1, max_length=10)]
    average_response_time: ServerMetricSummary
    p95_response_time: ServerMetricSummary
    average_wait_time: ServerMetricSummary
    max_queue_length: ServerMetricSummary
    simulation_end_time: ServerMetricSummary


class ServerPairedDifference(ServerContractModel):
    seed: Seed
    candidate_group_id: GroupId
    baseline_result_id: Digest
    candidate_result_id: Digest
    average_response_time_difference: SignedTime
    p95_response_time_difference: SignedTime
    average_wait_time_difference: SignedTime
    max_queue_length_difference: SignedCount
    simulation_end_time_difference: SignedTime


class ServerComparisonRun(ServerContractModel):
    group_id: GroupId
    result: ServerSimulationResult


class ServerComparisonResult(ServerContractModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["server_load_balancing"] = "server_load_balancing"
    result_kind: Literal["comparison"] = "comparison"
    comparison_id: Digest
    request: ServerComparisonRequest
    baseline_group_id: GroupId
    seeds: Annotated[tuple[Seed, ...], Field(min_length=1, max_length=10)]
    results: Annotated[tuple[ServerComparisonRun, ...], Field(min_length=2, max_length=80)]
    summaries: Annotated[tuple[ServerExperimentSummary, ...], Field(min_length=2, max_length=8)]
    paired_differences: Annotated[
        tuple[ServerPairedDifference, ...], Field(min_length=1, max_length=70)
    ]
    ranking_group_ids: Annotated[tuple[GroupId, ...], Field(min_length=2, max_length=8)]
    winner_group_ids: Annotated[tuple[GroupId, ...], Field(min_length=1, max_length=8)]
    primary_metric: Literal["average_response_time"] = "average_response_time"
```
<!-- M1_SCHEMA_END -->

ServerSimulationEvent 是 Pydantic 判别联合类型，使用 TypeAdapter(ServerSimulationEvent) 验证或生成独立事件 Schema；完整结果通过 ServerSimulationResult.model_json_schema() 包含其定义。它不是需要动态 fields/payload 字典的万能事件。

所有 tuple 通过 model_dump(mode="json") 序列化为 JSON 数组；顶层 ServerResult 用明确 scenario/result_kind 识别。版本号 1 是服务器场景自身的契约版本，不升级或冒用 Queue v1；旧 Queue JSON 不增加新字段。

## 8. Pydantic 跨字段规则与独立验证标准

字段草案不能替代下列规则。M2 中结构与局部关系属于 Pydantic model_validator，基于完整事件的复核属于独立 Validator；两层都需要测试。

| 对象/检查 | 必须实现的规则 |
| --- | --- |
| ServerSimulationConfig | 服务器 ID 唯一且严格升序，数量合法；速率和 capacity 边界；所有默认值也受校验 |
| ServerComparisonRequest | group_id 唯一；seeds 唯一且升序；各 config.seed=seeds[0]；相同负载；符合 comparison_axis；配置不重复；request-run 预算不超过 100000 |
| RequestRecord | arrival=dispatch≤start<end；wait=start−arrival；response=end−arrival；end=start+processing_time；duration>0 |
| ServerSimulationResult 结构 | samples/records 均恰好 N，ID 唯一且相同；规范排序；事件恰好 4N；sequence 从 0 连续；时间非递减；引用已知请求/服务器；completed=N；per_server 恰好覆盖全部服务器且升序 |
| ServerMetricSummary | minimum≤mean≤maximum；队列汇总不得超过 N；响应/结束时间汇总应为正；integer 峰值转 float 汇总不改变单次指标类型 |
| ServerComparisonResult 结构 | 恰好覆盖每个 (group_id, seed) 一次；run.config 等于对应组有效配置；seeds 与 request 一致；首组为 baseline；摘要/排名覆盖所有组且无重复；每个 candidate/seed 一条差值，引用正确 |
| 请求守恒 | 任意事件后：not_arrived+dispatching+queued+processing+completed=N；完整同刻批次后 dispatching=0 |
| 容量与占用 | 每个 server.active 至多一个请求；started 前 idle，completed 对应同一活动请求；事件 active/queue/total 三种计数与重建一致 |
| 分流正确性 | RR 按初始索引及每次 routed 更新；LO 按路由前的各节点 outstanding 和最小 ID 平局规则；同刻请求逐个更新状态 |
| FIFO 与及时启动 | started 必须取该节点 FIFO 队首；完整批次后不存在同一节点 idle 且有队列；不能套用“任何节点空闲时全局队列必须空”的 Queue 条件 |
| 生命周期 | 每个 ID 依次且仅一次 arrived/routed/started/completed；路由后 server_id 不变；事件时间与样本、记录对应 |
| 调度顺序 | 完成批次/旧等待启动/新到达逐个分流的顺序可从证据复核；同刻到达按 ID；同刻完成按 server/request ID |
| 异构处理时间 | 由样本 work 与所选节点 config.processing_rate 独立计算 duration；验证 start+duration=end；不调用引擎的时长函数 |
| 结果来源 | 请求参数与响应配置一致；工作负载哈希与样本一致；result_id 与配置、来源、元数据一致；generated 按版本规则复现样本，supplied 不假装从 seed 生成 |
| 指标 | 从 events 重建状态与 settled peaks；从 events/records 独立计算平均响应、P95、平均等待、结束时间和各节点完成数；不调用引擎 metrics 实现 |
| 配对与汇总 | 同 seed 下各组样本、workload_hash、workload_source 相同；各 seed 可有自己的样本；重算每组 mean/min/max、逐 seed 差值、ranking/winner |
| 固定种子 | 同配置、seed、样本来源与受支持运行版本得到相同完整 JSON；运行之间状态/RR 游标/RNG 不泄漏 |

样本及事件身份、sequence、顺序和同刻分组使用精确判断，不使用 isclose 将邻近时刻合并。记录中的事件时刻应直接引用同一存储值，必须精确相等。

对于重新进行浮点运算的数值复核，容差规则固定为 abs(a−b)≤max(1e−9, 4×max(ulp(a), ulp(b)))；非有限数直接失败。该规则不能用于放宽容量、FIFO、事件顺序或请求身份。实际调度先验证 duration 有限且正、end 有限且 end>start；浮点下溢或加法导致结束与开始重合时明确失败，不能增加最小服务时长或合并事件。

独立 Validator 只导入新契约与标准库，不导入 server_model/server_workload/server_comparison、SimPy 或 Queue 引擎。generated 样本复核按公开的 RNG 规范独立实现；规范变化需提升 workload_version。测试应提供已知种子样本向量，避免生成器与验证器同错而未发现。

## 9. 元数据、哈希和结果顺序

使用 SHA-256 的规范 JSON：sort_keys=True、separators=(",", ":")、ensure_ascii=True、allow_nan=False，UTF-8 编码。保存 float 原值，不做显示精度取整。

```text
workload_hash = digest({
  "workload_version": "server-workload-1",
  "samples": 按 (arrival_time, request_id) 排序的样本 JSON 数组
})

result_id = digest({
  "contract": "server-lb-result-v1",
  "config": 规范化的有效 ServerSimulationConfig,
  "workload_hash": workload_hash,
  "workload_source": generated 或 supplied,
  "metadata": ServerEngineMetadata
})

comparison_id = digest({
  "contract": "server-lb-comparison-v1",
  "request": 规范化的 ServerComparisonRequest,
  "runs": [{"group_id": ..., "seed": ..., "result_id": ...}, ...]
})
```

结果数组顺序固定为 seed 升序、每个 seed 内按 request.experiments 的组顺序。样本和记录按 (arrival_time, request_id)，per_server 按 server_id，摘要按组顺序，差值按 seed/候选组顺序。同一个配置置于不同 group_id 下不改变单 run 的 result_id，但整个比较 ID 会记录分组。

result_id 是来源标识，不能代替结果正确性验证；事件/指标内容的完整 SHA-256 由 ArtifactStore 收据机制保存。结果不带墙钟时间、随机 UUID 或凭据。新模型版本独立于应用版本，避免修改全局 __version__ 影响旧 Queue 结果 ID。

只承诺固定运行版本下的完整结果复现；跨 Python/SimPy/Pydantic 或模型规则版本不承诺字节一致。旧版本证据不能在恢复时静默按新模型规则重解释。

## 10. 手算案例与完整预期

以下都是 supplied 样本的手算规范，不是调度器执行结果。时间为秒、工作量为 normalized_work，均使用 seed 42 作来源标签。样本注入时随机 seed 不控制其内容。

### 10.1 案例 A：单服务器 FIFO

服务器：ID 1，速率 1，容量 1；RR。样本为 (ID, arrival, work)：(0,0,3)、(1,1,2)、(2,4,1)。

| 请求 | 节点 | 到达/分流 | 开始 | 完成 | 处理时长 | 等待 | 响应 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 0 | 0 | 3 | 3 | 0 | 3 |
| 1 | 1 | 1 | 3 | 5 | 2 | 2 | 4 |
| 2 | 1 | 4 | 5 | 6 | 1 | 1 | 2 |

预期 average_response_time=3，P95=4，average_wait_time=1，max_queue_length=1，completed_requests=3，simulation_end_time=6；节点 1 完成 3、队列峰值 1。

事件下表的 Q 是 total_queue_length_after_event。所有节点事件的关联队列数等于 Q；started.active=1，completed.active=0；routed 时请求 0 的 active=0，请求 1/2 的 active=1；arrived 的关联节点字段均 null。

| sequence | t | event_type | 请求 | 节点 | Q |
| --- | --- | --- | --- | --- | --- |
| 0 | 0 | request_arrived | 0 | null | 0 |
| 1 | 0 | request_routed | 0 | 1 | 1 |
| 2 | 0 | request_started | 0 | 1 | 0 |
| 3 | 1 | request_arrived | 1 | null | 0 |
| 4 | 1 | request_routed | 1 | 1 | 1 |
| 5 | 3 | request_completed | 0 | 1 | 1 |
| 6 | 3 | request_started | 1 | 1 | 0 |
| 7 | 4 | request_arrived | 2 | null | 0 |
| 8 | 4 | request_routed | 2 | 1 | 1 |
| 9 | 5 | request_completed | 1 | 1 | 1 |
| 10 | 5 | request_started | 2 | 1 | 0 |
| 11 | 6 | request_completed | 2 | 1 | 0 |

### 10.2 案例 B：两服务器 Round Robin

服务器 ID 1/2，速率均为 1、容量均为 1。游标初始 0。样本：(0,0,10)、(1,1,1)、(2,3,1)。

路由为 1、2、1；请求 2 不能转移到空闲的节点 2。

| 请求 | 节点 | 到达/分流 | 开始 | 完成 | 处理时长 | 等待 | 响应 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 0 | 0 | 10 | 10 | 0 | 10 |
| 1 | 2 | 1 | 1 | 2 | 1 | 0 | 1 |
| 2 | 1 | 3 | 10 | 11 | 1 | 7 | 8 |

预期 average_response_time=19/3≈6.333333333333333，P95=10，average_wait_time=7/3≈2.333333333333333，max_queue_length=1，completed_requests=3，simulation_end_time=11；节点 1 完成 2、峰值 1，节点 2 完成 1、峰值 0。

| sequence | t | event_type | 请求 | 节点 | Q |
| --- | --- | --- | --- | --- | --- |
| 0 | 0 | request_arrived | 0 | null | 0 |
| 1 | 0 | request_routed | 0 | 1 | 1 |
| 2 | 0 | request_started | 0 | 1 | 0 |
| 3 | 1 | request_arrived | 1 | null | 0 |
| 4 | 1 | request_routed | 1 | 2 | 1 |
| 5 | 1 | request_started | 1 | 2 | 0 |
| 6 | 2 | request_completed | 1 | 2 | 0 |
| 7 | 3 | request_arrived | 2 | null | 0 |
| 8 | 3 | request_routed | 2 | 1 | 1 |
| 9 | 10 | request_completed | 0 | 1 | 1 |
| 10 | 10 | request_started | 2 | 1 | 0 |
| 11 | 11 | request_completed | 2 | 1 | 0 |

本案例所有关联节点事件的节点等待数都等于表中的 Q；三个 routed 的 active 分别为 0、0、1。arrived 的关联节点字段均 null。

### 10.3 案例 C：两服务器 Least Outstanding

服务器和请求样本与 B **完全相同**。t=0 两节点 outstanding 都为 0，选 ID 1；t=1 为 (1,0)，选 ID 2；t=3 为 (1,0)，仍选 ID 2。

| 请求 | 节点 | 到达/分流 | 开始 | 完成 | 处理时长 | 等待 | 响应 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1 | 0 | 0 | 10 | 10 | 0 | 10 |
| 1 | 2 | 1 | 1 | 2 | 1 | 0 | 1 |
| 2 | 2 | 3 | 3 | 4 | 1 | 0 | 1 |

预期 average_response_time=4，P95=10，average_wait_time=0，max_queue_length=0，completed_requests=3，simulation_end_time=10；节点 1 完成 1、峰值 0，节点 2 完成 2、峰值 0。

注意：请求 2 在 4 秒完成，但请求 0 到 10 秒才完成，**总完成时间是 10，不是 4**。P95 未改善；这个夹具只能证明特定请求及平均响应的变化，不能声称所有指标均改善。

| sequence | t | event_type | 请求 | 节点 | Q |
| --- | --- | --- | --- | --- | --- |
| 0 | 0 | request_arrived | 0 | null | 0 |
| 1 | 0 | request_routed | 0 | 1 | 1 |
| 2 | 0 | request_started | 0 | 1 | 0 |
| 3 | 1 | request_arrived | 1 | null | 0 |
| 4 | 1 | request_routed | 1 | 2 | 1 |
| 5 | 1 | request_started | 1 | 2 | 0 |
| 6 | 2 | request_completed | 1 | 2 | 0 |
| 7 | 3 | request_arrived | 2 | null | 0 |
| 8 | 3 | request_routed | 2 | 2 | 1 |
| 9 | 3 | request_started | 2 | 2 | 0 |
| 10 | 4 | request_completed | 2 | 2 | 0 |
| 11 | 10 | request_completed | 0 | 1 | 0 |

三个 routed 的关联队列数都为 1、active 都为 0，其余关联队列数为 0。所有批次结算后的 Q 均为 0。

以 B 为 baseline、C 为 candidate，seed 42 的配对差值预期：平均响应 −7/3、P95 0、平均等待 −7/3、总队列峰值 −1、总完成时间 −1。ranking=[lo, rr]，winner=[lo]。两个 run 的样本与 workload_hash 必须完全一致，config 仅策略不同。

### 10.4 补充案例 D：异构速率

batch 到达 0，两个请求工作量均为 2；RR；服务器 1 速率 1、服务器 2 速率 2。

请求 0→节点 1，start=0、duration=2、end=2、response=2；请求 1→节点 2，start=0、duration=1、end=1、response=1。预期平均响应 1.5、P95 2、平均等待 0、总/各节点队列峰值 0、完成数 2、总完成时间 2。该案例防止错误地把样本 work 当作固定 service_time。

### 10.5 补充同刻与平局验收

- 单服务器样本 (0,0,1)、(1,0.5,1)、(2,1,1)：t=1 必须依次完成 0、启动旧等待 1、到达/分流新请求 2；请求 1/2 在 2/3 秒完成。
- 两同速服务器、LO、同时到达请求 ID 9 与 ID 2，工作量都为 1：先 ID 2→节点 1 并开始，再 ID 9→节点 2；t=1 完成顺序为节点 1 的 ID 2、节点 2 的 ID 9。
- 在 t=1 有两个同刻完成时，新请求必须在两节点都释放、旧等待都开始之后选择；不是在第一个完成回调中提前选择。
- 注入样本 ID 非连续、服务器 ID 非连续时，以上排序和平局规则不变；不能用数组下标冒充真实 ID。

## 11. LangGraph / MCP / Registry 接口设计

完整链路保留为：

```text
自然语言
→ ModelPlanner（本地已注册场景说明 + 实际发现且契约匹配的 MCP Schema）
→ 场景参数校验与设计锁定
→ MCP Client.call_tool
→ MCP Server 对工具名分派
→ 对应独立 Simulation Engine
→ 对应独立 Validator
→ 报告及该场景回放/可视化
```

保留旧工具 run_simulation / compare_simulations。Server 工具为 run_server_simulation / compare_server_simulations；输入分别直接是 ServerSimulationConfig / ServerComparisonRequest 的字段，输出分别是 ServerSimulationResult / ServerComparisonResult。LLM 的规划信封由现有 Planner 拆解，不能传给 MCP Server；模型不能提供 samples/records/events/metrics 作为计算结果。

最小静态 Scenario Registry 登记场景 ID、支持/不支持范围、工具名、输入输出模型、支持版本和默认假设。契约目录不导入引擎；Server 自己拥有引擎 handler 映射。Agent 按注册工具名获得参数/结果适配，运行时不直接调用引擎。

场景选择必须来自 ModelPlanner 的 AIMessage.tool_calls；消费 MCP 发现的 Schema，沿用 ToolMessage 的真实工具结果与调用 ID。不能用关键词选择引擎后写一条虚构的模型调用记录。关键词可以用于 Demo 的拒绝保护或界面帮助，不能代替正式模型选择。

未知工具、混合场景、不支持的超时 / 故障 / 扩缩容问题或无法解析的实验需求会明确拒绝，不回退 Queue Demo，也不替换目标继续执行。当前没有交互式澄清节点。含糊的到达参数可采用已披露默认值；超出支持机制的目标则不能靠默认值消解。

首个合法参数计划后锁定 scenario、experiment_goal、comparison_axis、group_id/组配置（除 seed）、负载、主指标和支持版本。种子单独保存为只能增加的集合；结果重规划只能保留原 seed 并增加 seed，组配置中的 seed 同步为排序后 seeds[0]，不改变其他锁定设计。参数错误阶段可以修正无效计划，但不能悄悄改变用户明确要求的场景或目标。旧 checkpoint 缺失场景字段时，按旧 Queue 契约兼容读取，不能给原 JSON/历史证据添加字段。

LangGraph 保持现有 planner → parameter_validator → executor → result_validator → report 节点和条件路由。场景分派和证据充分性在节点内部处理；Registry 不是图节点。

`compare_server_simulations` 容量比较参数示例：

```json
{
  "comparison_axis": "server_pool",
  "experiments": [
    {
      "group_id": "one_server",
      "config": {
        "requests": 1000,
        "servers": [{"server_id": 1, "processing_rate": 1.0}],
        "routing_strategy": "round_robin",
        "seed": 42
      }
    },
    {
      "group_id": "three_servers",
      "config": {
        "requests": 1000,
        "servers": [
          {"server_id": 1, "processing_rate": 1.0},
          {"server_id": 2, "processing_rate": 1.0},
          {"server_id": 3, "processing_rate": 1.0}
        ],
        "routing_strategy": "round_robin",
        "seed": 42
      }
    }
  ],
  "seeds": [42, 43, 44, 45, 46]
}
```

双方省略的 arrivals/workload 使用本文件的默认假设并由规范化配置完整保存。策略比较用 comparison_axis=routing_strategy、相同服务器集合，以及不同 routing_strategy；不新增手写 LLM 参数 Schema。

## 12. 假设、局限和风险

| 项目 | 处理 |
| --- | --- |
| 有限开放到达、最终排空 | 不解释为长期稳态；最终完成数固定 N，只用于守恒/进度，不能当作本模型的成功率差异 |
| 一个节点一个处理槽 | 不宣称模拟真实多线程、CPU 时间片或并发争用；capacity>1 明确拒绝 |
| 零耗时分流和传输 | 路径动画是示意，不得把视觉移动时间加入响应时间 |
| 无限业务队列 | 不支持拒绝和队列溢出；内存仍由有限请求数和 request-run 预算约束 |
| 速率固定且节点始终在线 | 不支持故障、冷启动、健康检查、自动扩缩容 |
| LO 不看速率或工作量 | 异构节点下未必优于 RR，不自动升级为加权策略，不为展示追求赢家 |
| 指数样本是假设 | 无现实校准；极端数值明确失败而不是改变分布/时间 |
| 浮点同刻 | 精确值分组、明确阶段、表示检查；容差只用于重算数值，不改变时钟或顺序 |
| 双实现验证同错 | 固定手算事件表、已知 RNG 向量、合法形状篡改测试、独立指标实现共同约束 |
| JSON 联合契约 | 浏览器按 Server 事件标签选择对应 Schema；Queue 检查器不能替代 Server 契约验证 |
| 公开时间线 | 使用实际调用和 checkpoint 来源；不复制模型自由文本或补造执行步骤 |

RR 按请求数量轮换，LO 按当前在途请求数选节点；两者都不保证最小响应时间。B/C 手算显示某一负载下平均响应改善，同时 P95 不变，正好应当保留这种结果边界。

## 13. 设计验证

Pydantic 2.14.0 编译字段草案并生成八个数据模型的 JSON Schema。
配置和比较请求的 JSON 往返、18 个严格字段及事件拒绝案例通过。
A/B/C 各 12 个手写事件的生命周期、计数及 settled 队列峰值通过交叉复核；
A–D 的响应、等待、P95 和结束时间算术与本文一致。

这些检查验证字段草案和手算规范，使用内存中的占位哈希，
不代表执行过模拟或通过结果来源验证。
真实引擎及独立验证器的验收见 [Server Engine](server-load-balancing-m2.md)。
## 14. 依据

- 当前项目契约：`src/sim2clip/simulation/schemas.py`、`src/sim2clip/simulation/workload.py`。
- 当前协议和编排：`src/sim2clip/mcp_server/simulation_server.py`、`src/sim2clip/agent/planner.py`、`src/sim2clip/agent/graph.py`。
- [Pydantic 字段级严格校验](https://docs.pydantic.dev/latest/concepts/strict_mode/#at-the-field-level)：标量 strict 约束与入口行为。
- [Pydantic 判别联合](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)：带标签的参数/事件类型。
- [SimPy 时间与调度](https://simpy.readthedocs.io/en/stable/topical_guides/time_and_scheduling.html)：同刻事件依次执行，其触发顺序不能自动代表本项目的业务优先级。
- MCP 工具契约规范：每个工具提供精确输入和结构化输出 Schema。
