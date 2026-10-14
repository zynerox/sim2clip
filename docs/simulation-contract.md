# Simulation Engine 契约 v1

本文件定义 Queue 模型的公共数据结构及事件语义。MCP Server、LangGraph
State 和 Visualization 使用同一份 Pydantic 模型。引擎仅依赖 Pydantic、SimPy
和 Python 标准库，不访问模型、MCP 或网络。

## 运行入口

```python
generate_workload(config: SimulationConfig) -> tuple[CustomerSample, ...]
run_simulation(
    config: SimulationConfig,
    samples: Sequence[CustomerSample] | None = None,
) -> SimulationResult
run_comparison(
    experiments: Sequence[SimulationConfig],
    *, seeds: Sequence[int] | None = None,
) -> ComparisonResult
```

`run_simulation` 在 samples 为 None 时生成样本；否则验证并使用显式样本。
每次执行创建新的 SimPy Environment 和 Resource。样本进入调度之前按
`(arrival_time, customer_id)` 排序，确定同时到达的先后顺序。
收银台 ID 从 1 开始，选择编号最小的空闲收银台；服务完成释放编号。

## 六个共享 Schema

### SimulationConfig

`schema_version="1"`、`simulation_type="queue"`、
`scenario="convenience_store_rush"`、`cashiers`、`customers`、`seed`、
`arrival_distribution="exponential"`、`mean_interarrival_time`、
`service_distribution="exponential"`、`mean_service_time`、
`time_unit="second"`、`queue_discipline="fifo"`。

默认 100 人、种子 42、平均到达间隔 10 秒、平均服务时长 25 秒。
cashiers 必填。整数为严格整数，拒绝布尔值和数字字符串；时间必须有限。
所有模型禁止额外字段且不可变。公共入口重新验证模型实例，防止
Pydantic `model_copy(update=...)` 的未验证内容绕过约束。

### CustomerSample

`customer_id`：非负且在实验内唯一；`arrival_time`：非负秒数；
`service_time`：严格正秒数。生成 ID 从 0 开始，允许注入其他非负 ID。
官方生成器首个顾客在 0 秒到达，到达和服务使用独立的 SHA-256 派生随机数流。
不会修改全局 random 状态，不使用跨进程变化的 Python hash()。

### CustomerRecord

包含 `customer_id`、`cashier_id`、`arrival_time`、`service_time`、
`service_start_time`、`service_end_time`、`wait_time`。
满足 arrival <= start < end，wait = start - arrival。
service_time 为原始样本时长，与 end - start 在浮点容差内一致。

### SimulationEvent

六个字段均必需：`sequence`、`timestamp`、`event_type`、`customer_id`、
`cashier_id`、`queue_length_after_event`。到达事件 cashier_id 必须为 null；
开始和完成服务事件必须给出收银台编号。

| 事件 | 回放状态变更 | 队列变更 |
| --- | --- | --- |
| customer_arrived | 顾客进入等待状态 | +1 |
| customer_started | 队首顾客进入指定收银台 | -1 |
| customer_finished | 顾客离开收银台，进入完成状态 | 0 |

sequence 从 0 连续递增，timestamp 非递减。任意事件前缀都满足：
已到达人数 = 等待人数 + 服务人数 + 完成人数；服务人数不超过容量；
一个收银台只能服务一个顾客。EventRecorder 拒绝非法转换，校验失败不改变状态。

**同一时间戳的含义：** sequence 是因果回放顺序，不能自行按事件类型重排。
到达和即时开始服务是两个事件，因此 queue_length_after_event 可以出现一个
零时长过渡。按模拟时间绘制时，处理完 timestamp <= 当前时刻的全部事件后绘图。
max_queue_length 取每个时间戳最后事件的队列长度。不能直接取所有事件队列字段的最大值。

例：3 人同时到达、3 台收银台，事件前缀中会出现等待人数 1、2、3，
随后同一时刻变为 2、1、0；最终 max_queue_length 为 0。
调试逐事件视图可显示中间状态，业务时间线显示该时刻的最终状态。

### SimulationResult

包含 `schema_version`、`result_id`、`config`、`metadata`、`workload_source`、
`workload_hash`、`customer_samples`、`customer_records`、`metrics`、`events`。
samples 和 records 按样本的到达顺序保存，events 按 sequence 保存。
所有 Tuple 转为标准 JSON 数组，无 Python 专有对象。

metrics 包含：

| 字段 | 单位与定义 |
| --- | --- |
| average_wait_time | 秒；所有顾客 start - arrival 的平均值 |
| average_service_time | 秒；所有顾客 end - start 的平均值 |
| max_wait_time | 秒；最大等待时长 |
| max_queue_length | 人；每个时间戳处理完后的最大等待人数 |
| completed_customers | 人；完成顾客数 |
| simulation_end_time | 秒；最后完成时间，从 0 起算 |

metadata 保存引擎、样本算法、Python、SimPy、Pydantic 版本。
不包含墙钟时间或随机 ID，相同输入与版本得到相同 JSON。
result_id 和 workload_hash 使用规范 JSON 的 SHA-256，原始浮点不提前四舍五入。
生成与注入样本通过 workload_source 区分；注入样本时 seed 仅保留在配置中，
真实随机输入由完整 customer_samples 和 workload_hash 描述。

### ComparisonResult

包含 schema_version、comparison_id、request、baseline_cashiers、seeds、完整 results、
summaries、paired_differences、ranking_cashiers、winner_cashiers 和 primary_metric。
primary_metric 固定为 average_wait_time；首个输入实验是 baseline。

每个种子只生成一批顾客样本，各组共享；不同种子生成不同样本。
返回结果顺序为种子顺序，再按输入实验顺序。
比较参数必须仅在 cashiers 和被显式 seeds 覆盖的 seed 上不同。
支持 2–8 组、1–10 个唯一种子，总 customer-runs <= 100000。

summaries 按组输出每次平均等待、每次最大队列和每次完成时间的 mean / minimum /
maximum。**max_queue_length.mean 是多次峰值的平均数，不是一个整数峰值。**
每次原始整数峰值保留在对应 SimulationResult 中。

差值采用 candidate - baseline；wait_time_reduction_percent 采用
(baseline_wait - candidate_wait) / baseline_wait * 100。baseline_wait 为 0 时返回 null，
不会制造百分比。排名按均值升序，均值在 1e-9 相对/绝对容差内视为并列。
winner_cashiers 保留所有并列组。无需根据收银台数量预设赢家。

这些都是样本观察，不是置信区间或显著性判断。Agent 后续可读取逐种子差值，
决定是否追加实验；引擎本身不规划、不循环调用 LLM、不改变实验目标。

## 手算测试夹具

3 人同时在 0 秒到达，服务时长按 ID 为 3、2、1 秒：

| 收银台 | 等待时长 | 平均等待 | 最大队列 | 最后完成 |
| --- | --- | --- | --- | --- |
| 1 | 0、3、5 秒 | 8/3 秒 | 2 人 | 6 秒 |
| 3 | 0、0、0 秒 | 0 秒 | 0 人 | 3 秒 |

样本注入机制允许手算及历史数据回放。超出浮点可表示范围的服务完成时间
显式报错，不输出零服务时间或非有限值。

## Queue MCP 接口

1. `run_simulation`：参数采用 SimulationConfig 的字段；Server 解析模型后调用公共
   run_simulation；返回完整 SimulationResult。最小参数为 cashiers，其余可默认，
   兼容 cashiers / customers / seed 三字段输入。
2. `compare_simulations`：输入 ComparisonRequest（experiments + seeds）；调用
   run_comparison(request.experiments, seeds=request.seeds)；返回 ComparisonResult。
3. JSON 输出使用 model_dump(mode="json")；工具 Schema 由共享模型派生，
   不拼接字符串、不重命名 timestamp/event_type，不重复定义数据模型。
4. ValidationError 属于参数错误；数值范围或运行错误属于执行错误。Server 将它们
   转成 MCP 错误，并保留错误类型；不能返回伪造的成功指标。
5. 缓存与子进程生命周期属于 Server / Client。引擎接口保持纯计算，
   不读取缓存、不启动服务、不自动写文件。

模型结构校验不代替独立 Result Validator；后者还需从事件复核指标、输入来源和比较结论。
