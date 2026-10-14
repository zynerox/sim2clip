# Qwen3.5-Plus：真实模型验收

模型为百炼北京地域 `qwen3.5-plus`，使用 OpenAI-compatible
Chat Completions 和原生 Function Calling。两轮初测及一次修复后复验累计 **12 次真实请求**，
覆盖 Queue、Server 和全部四个 MCP 工具。

| 批次 | 请求数 | 完整任务统计 |
| --- | ---: | --- |
| 原始 R1 两轮 | 10 | 8 个支持任务中 **6/8** 通过；另有 E/F2 两个拒绝案例 |
| 修复后 G/H | 2 | 完整单次任务 **2/2** 通过 |
| 累计 | **12** | 分批记录，不将原始 6/8 改为 8/8 |

原始 G/H 的工具、参数和模拟结果正确，但公开报告将单次任务误写为比较。
修复后的本地结果重建与后续真实复验分别记录。
E/F2 只验证零工具安全拒绝，原生拒绝正文未保存，**完整解释文本未验证**。
**真实验收没有触发重规划**，本地纠错、恢复及失败测试不计入远程模型通过率。

本文汇总原始验收、本地报告修复及 G/H 真实复验的工具、参数、指标和验证结论。
完整运行日志保留在本地；私有 checkpoint、原始消息、请求头和隐藏推理不随公开资料分发。

## 配置与请求边界

```dotenv
MODEL_API_KEY=your-api-key
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen3.5-plus
```

`ModelSettings.load` 从当前目录 .env 或指定 env-file 读取配置，进程环境优先；
SDK 在根地址后追加 /chat/completions。真实运行使用网络 Transport、原 ModelPlanner、
stdio MCP、SimPy、LangGraph 与独立 Validator。

验收设置：max_completion_tokens=4096、enable_thinking=false、模型超时 60 秒、
单案例 watchdog 240 秒、请求体上限 64,000 字节、SDK/HTTP 自动重试 0。
请求次数在发送前持久化，失败、超时和取消仍计数。
首批 A/B/E 上限 6 次，剩余七例上限 14 次，G/H 复验上限 4 次；均为每例最多 2 次。
实际请求分别为 3、7、2 次。

验收脚本的 `prepare` 只检查配置并估算费用，不调用模型。
`smoke`、`full`、`reverify` 需要显式 `--authorize-remote`，且拒绝覆盖已有输出目录；
这些入口会请求外部模型，复验还要求完整的真实初测证据。

## 兼容性依据

以下为验收当日核对的官方资料及本地 SDK 序列化结果，不代表永久不变的供应商配置：

- [qwen3.5-plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-5-plus)：验收使用北京地域的 `qwen3.5-plus` 别名与 Function Calling；供应商别名和支持范围可能变化。
- [OpenAI-compatible Chat 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)：Qwen3.5-Plus 支持 `max_completion_tokens`，包含思考与回答；扩展 `enable_thinking` 经 `extra_body` 传入。官方说明实际输出与上限可能有最多 10 Token 的差异。
- [Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：标准 `tools` 与 `tool_calls[].function.arguments` JSON 字符串。工具可不被调用，不强制 `tool_choice=required`，以允许不支持问题拒绝。
- [思考模式](https://help.aliyun.com/zh/model-studio/deep-thinking)：Qwen3.5-Plus 默认开启思考；R1 显式设为 false。思考输出属于输出 Token，不重复加到 completion_tokens 上。

本机 LangChain/OpenAI SDK 源码与本地 HTTP 请求验证：`ChatOpenAI(max_completion_tokens=4096, extra_body={"enable_thinking": False})` 的实际 JSON 顶层包含对应两字段；无额外 `max_tokens`、不使用 Responses API、不流式、不自动重试。原生 JSON 工具调用被 SDK 解析为 AIMessage.tool_calls，然后仍由原 ModelPlanner 校验信封、平铺 request，交给原参数验证节点。

SDK 中未知扩展 / 思考字段不作为规划、指标或公开文本。没有工具调用时，Planner 返回 UNSUPPORTED_REQUEST；非法 JSON / 信封进入有界重规划。当前没有交互式澄清节点，默认假设由结构化配置和报告披露，不支持需求以失败状态拒绝。

## 案例矩阵

| ID | 输入 | 工具/期望 | 预计模型请求 |
| --- | --- | --- | --- |
| A | 100 位顾客排队，比较 1 个收银台和 3 个收银台的平均等待时间。 | compare_simulations；100顾客、1/3、Queue Schema、共享样本、独立验证与报告 | 1–2 |
| B | 1000 个请求进入系统，比较 1 台和 3 台服务器的响应时间。 | compare_server_simulations；server_pool、1000请求、1/3节点、Server契约/引擎/验证器 | 1–2 |
| C | 同样的两台服务器，Round Robin 和 Least Outstanding 哪个平均响应时间更低？ | compare_server_simulations；routing_strategy、同一池与负载，不预设赢家 | 1–2 |
| D1 | 服务器老是排队，多加两台机器能改善多少？ | 服务器容量比较；基线缺失，须披露假设，不暗示生产实测 | 1–2 |
| D2 | 收银口只开一个太堵了，三个口能让这 100 位顾客少等多久？ | Queue 1/3；非模板表达 | 1–2 |
| E | 帮我模拟明天的天气变化。 | 无工具、UNSUPPORTED_REQUEST、无虚构天气结果 | 1 |
| F1 | 比较 1 台和 3 台服务器，其他参数用默认值。 | Server容量；披露人口/分布/速率/种子默认值 | 1–2 |
| F2 | 模拟 -5 个请求，比较 1 台和 3 台服务器。 | 拒绝或确定性校验拦截；零非法MCP执行，不能改成默认人口继续 | 1–2 |
| G | 用 2 个收银台模拟 20 位顾客排队。 | run_simulation | 1 |
| H | 用两台服务器和 Round Robin 策略模拟 20 个请求。 | run_server_simulation | 1 |

Registry 默认：Queue 指数到达均值10秒、服务均值25秒；Server 1000请求、指数到达均值0.01秒、工作量均值0.025、节点速率1 work/秒。C 默认两台同速；容量默认1/3；随机比较至少五配对种子。是否接受/采用这些假设由真实模型计划与确定性校验检验，不由测试入口替模型填参数。D1 的“多加两台”不是模型内动态扩缩容规则，应说明比较两个固定池。

若远程模型直接拒绝 F2，则“非法参数拦截/重规划”在该远程案例未触发；本地故障注入另列，不能伪装远程纠错证据。

## 原始 R1：逐案例结果

两轮请求均返回 HTTP 200，模型自动重试及重规划均为 0。
正例参数首次合法，真实 MCP 各执行一次且 cached=false，独立验证 valid/sufficient=true。

| 案例 | 正确场景 / 首次原生工具 | 首次参数 | MCP / 独立验证 | 工作流与用户可见结果 | 原始严格验收 |
| --- | --- | --- | --- | --- | --- |
| A | queue / compare_simulations | 合法 | 1次 / 通过 | completed；100顾客、1/3收银台报告 | 通过 |
| B | server_load_balancing / compare_server_simulations | 合法 | 1次 / 通过 | completed；1000请求、1/3服务器报告 | 通过 |
| C | server_load_balancing / compare_server_simulations | 合法 | 1次 / 通过 | completed；相同两台服务器、RR/LO共享负载比较报告 | 通过 |
| D1 | server_load_balancing / compare_server_simulations | 合法 | 1次 / 通过 | completed；假设当前1台，增加2台得到3台；1000请求和工作负载默认值披露 | 通过 |
| D2 | queue / compare_simulations | 合法 | 1次 / 通过 | completed；口语输入识别100顾客、1/3收银台，披露到达/服务假设 | 通过 |
| E | 无场景 / 无工具 | 不适用 | 0 / 未进入 | failed；UNSUPPORTED_REQUEST、通用能力说明，无天气结果 | 拒绝行为通过；天气说明措辞未验证 |
| F1 | server_load_balancing / compare_server_simulations | 合法 | 1次 / 通过 | completed；默认1000请求、RR、指数到达0.01秒/work均值0.025、节点速率1，1/3台比较 | 通过 |
| F2 | 无场景 / 无工具 | 不适用 | 0 / 未进入 | failed；UNSUPPORTED_REQUEST、通用能力说明；-5未改为合法数量、零非法执行 | 拒绝行为通过；负数说明措辞未验证 |
| G | queue / run_simulation | 合法 | 1次 / 通过 | completed；20顾客、2收银台、单次seed42；原公开目标误写“比较” | 输出文案失败；另存修正版 |
| H | server_load_balancing / run_server_simulation | 合法 | 1次 / 通过 | completed；20请求、两台、RR、单次seed42；原公开目标误写“比较” | 输出文案失败；另存修正版 |

C实际比较轴routing_strategy，两个配置的服务器池、请求量、到达/工作量设置相同；其他容量比较轴server_pool，策略与工作负载设置保持一致。每个比较5个配对种子、10次SimPy运行；逐种子完整样本与workload_hash相同，并从收据加载再验证。G/H仅1次SimPy运行，无比较组。这些请求没有重规划，因此跨规划轮锁定机制没有被实际扰动检验；不能声称验证了所有锁定/纠错分支。

| 第二轮已验证数值（秒） | 基线或单次 | 比较候选 |
| --- | ---: | ---: |
| C：相同两台，平均响应 | RR 1.490388 | LO 1.467459 |
| D1：1/3台，平均响应 | 7.797612 | 0.107930 |
| D2：1/3收银台，平均等待 | 789.520139 | 33.271837 |
| F1：默认1/3台，平均响应 | 7.797612 | 0.107930 |
| G：两收银台20顾客，单次平均等待 | 13.401483 | 不适用 |
| H：两台RR服务器20请求，单次平均响应 | 0.055057 | 不适用 |

C在这组五个共享种子下LO均值略低，结论由验证后的指标得出，配置没有预设胜者；不据此推断所有工作负载中LO必胜或统计显著性。D1/F1与B默认配置相同，D2与A相同，使用同一确定性种子后数值相同是预期现象；每例仍有独立真实模型请求及cached=false的MCP执行，不是复用历史缓存。

E/F2 的模型未返回工具；Planner 输出 UNSUPPORTED_REQUEST。原生拒绝正文未持久化，
只能核实零工具执行和客户端能力提示，不能核实模型是否具体解释天气限制或负数原因。
F2 没有触发远程非法参数校验或重规划。初始客户端提示有重复错误行；该问题在本地输出修复中处理。

| 指标与分母 | 初始真实结果 |
| --- | --- |
| 支持问题场景选择 | 8/8=100% |
| 场景行为，含两例无场景拒绝 | 10/10=100% |
| 工具选择，含两例无调用预期 | 10/10=100%；四工具均有真实证据 |
| 正例参数首次合法 | 8/8=100%；负例无参数不计分母 |
| 正例工作流完成与独立验证 | 8/8=100% |
| 严格完整任务：含公开报告准确性 | **6/8=75%**；G/H初始文案失败 |
| 非法任务安全拒绝 | F2 1/1；零非法执行，未触发远程参数纠错 |
| 不支持任务安全拒绝 | E 1/1；零错误场景/天气结果 |
| 拒绝具体原因措辞 | 0/2已验证，2/2未验证；不是证明模型解释错误 |
| 模型请求 / 自动重试 / 重规划 | **10 / 0 / 0** |
| 总Token / 毛费用估算 | **65638 / ¥0.0693144** |

没有真实HTTP/模型兼容性失败，没有实际验证器拒绝合法结果或引擎错误。E/F2是模型无工具的预期拒绝，由Planner转为失败状态；G/H初始错误是输出文案。没有已证实的工具规划缺陷，但这个小样本、固定五种子、一次请求成功不证明任意自然语言鲁棒性；设计跨轮锁、非法参数重规划、供应商失败分支仍未在远程触发。通用且重复的拒绝错误、拒绝原文证据缺失，是明确的解释体验/审计缺陷。

## 工具调用与结果来源

`audit.json`同时观察HTTP `tool_calls[].function`及SDK转换后的`AIMessage.tool_calls`，均在原Planner平铺request/重写ID之前。A/B各一条同名原生函数调用，arguments_json_valid=true、envelope_and_request_schema_valid=true、invalid_tool_call_count=0；E两处均为空数组。不是最终报告反推工具选择。

| 案例 | 返回AIMessage.tool_calls的安全投影 | 参数验证后真实MCP请求摘要 |
| --- | --- | --- |
| A | name=compare_simulations；request.experiments=[{cashiers:1,customers:100,seed:42},{cashiers:3,customers:100,seed:42}]；seeds=[42,43,44,45,46]；假设数量5 | schema_version=1、queue/convenience_store_rush、FIFO、指数到达均值10秒、指数服务均值25秒；人口/容量完全匹配问题 |
| B | name=compare_server_simulations；comparison_axis=server_pool；两config各requests=1000、servers数量1/3、routing_strategy=round_robin、seed=42；seeds=[42,43,44,45,46]；假设数量7 | server_queue_network/server_load_balancing；每节点processing_rate=1、capacity=1；指数到达均值0.01秒、指数work均值0.025；FIFO、complete_then_dispatch、second/normalized_work；未映射便利店 |
| E | AIMessage.tool_calls=[]；HTTP无function调用，finish_reason=stop；response_text_present=true | 不生成参数、不执行MCP；Planner转换成UNSUPPORTED_REQUEST并停止 |

A/B各真实MCP一次，status=success、cached=false、receipt_verified=true。收据及响应SHA：

- A：`A/runs/A/r1-c0-b2c92dfc904c249b3e794dfd8b465b2450ae0bcf9998e18178dda472a8cc579e.receipt.json`；响应 `43a2a652bbfc87ac2dd777d1f47fe1fde7cc24537e0ce61e31dc9f7ec50b5003`。
- B：`B/runs/B/r1-c0-d643796e5b36e7a87661fea4da46381a255de17362d8485b1b81dd866f9f2be8.receipt.json`；响应 `57dd087cfc19dec0bace7575073700f84d4c03282a77a3af56f7f82dffbeb66f`。

ArtifactStore校验响应SHA后，按原ToolContract解析并再次调用独立validate_tool_response；presentation/result.json与该结果完全相等。每例2组×5种子=10次SimPy运行；每个种子的两个组工作负载hash与完整样本相同。全部产物非空，review.json保存字节数及SHA。

A/B 节点顺序为 planner → parameter_validator → executor → result_validator → report，
均为 round=1。每个比较包含 2 组 × 5 个种子；每个种子的两组使用同一批完整样本和工作负载哈希。
参数来自模型计划并经确定性校验，指标来自经过独立验证的引擎结果。

| 模型假设下的配对种子均值 | 1个收银台/服务器 | 3个收银台/服务器 | 两组均值之比的降幅 |
| --- | ---: | ---: | ---: |
| A：平均等待秒 | 789.520139 | 33.271837 | 95.785815% |
| B：平均响应秒 | 7.797612 | 0.107930 | 98.615854% |

降幅是 `(基线均值−候选均值)/基线均值`，不冒充逐种子降幅均值。报告披露了工作负载/服务/路由假设、共享样本和描述性限制；数值来自验证后的SimPy结果，没有LLM编造或人工替换。5个种子不证明统计显著性或真实门店/生产服务器表现。

## 初测 Token、费用与耗时

费用采用验收记录中的历史北京地域价格估算：输入 0.8 元、输出 4.8 元/百万 Token，
适用这些请求的 ≤128K 输入档，不代表当前费率。价格资料见 [模型官方资料](https://help.aliyun.com/zh/model-studio/qwen3-5-plus)。
不扣免费额度、缓存或活动优惠；实际账单及免费额度余额未知。价格估算不构成扣费上限。

| 案例 | 输入 / 输出 / 总Token | 模型HTTP秒 | 整案例秒 | 毛费用（元） |
| --- | --- | ---: | ---: | ---: |
| A | 6152 / 277 / 6429 | 7.088 | 10.906 | 0.0062512 |
| B | 6150 / 649 / 6799 | 12.913 | 74.198 | 0.0080352 |
| C | 6143 / 662 / 6805 | 12.825 | 33.931 | 0.0080920 |
| D1 | 6137 / 641 / 6778 | 13.421 | 75.876 | 0.0079864 |
| D2 | 6150 / 439 / 6589 | 8.738 | 12.092 | 0.0070272 |
| E | 6132 / 149 / 6281 | 4.694 | 5.979 | 0.0056208 |
| F1 | 6144 / 631 / 6775 | 12.574 | 74.465 | 0.0079440 |
| F2 | 6145 / 92 / 6237 | 3.051 | 4.303 | 0.0053576 |
| G | 6142 / 297 / 6439 | 6.862 | 8.686 | 0.0063392 |
| H | 6142 / 364 / 6506 | 7.444 | 9.303 | 0.0066608 |
| 第一轮3次 | 18434 / 1075 / **19509** | 24.695 | 91.084 | **0.0199072** |
| 第二轮7次 | 43003 / 3126 / **46129** | 64.915 | 218.656 | **0.0494072** |
| 两轮10次 | 61437 / 4201 / **65638** | 89.610 | 309.740 | **0.0693144** |

所有usage均由供应商返回，无缺失；输出没有思考Token额外计数。费用按上述北京原价计算，约0.0693元，不扣免费额度或缓存优惠，不是实际扣款/硬账单上限。整案例时间加总排除父子进程启动间隙，HTTP时间是客户端往返，不能称为服务端纯推理时间。后续本地复核、文案修复与重导出不产生模型请求，也不加入原始案例计时。

### 本地执行阶段计时

B只读Checkpoint可恢复：初始状态→planner 13.022704秒（其中已测HTTP12.913079秒）；参数0.011114；executor45.657045；result_validator2.613652；report3.275252。上述节点跨度合计64.579767秒，与整例74.198469秒之间约9.618702秒包含初始化、后置复核、导出、关闭等，旧记录无法再拆分。**executor跨度包含MCP/SimPy/传输/SDK结构验证/转换/本地收据写入等，不能认定为SimPy计算或纯MCP往返时间。**

第二轮增加直接perf_counter计时，保存在timings中；结果节点和report节点仍为Checkpoint间隔，含调度、校验与I/O。表中的额外独立验证是R1导出前的validate_tool_response调用，不冒充图内验证；公开报告格式化也不等于整个report节点。

| 案例 | HTTP秒 | executor节点跨度秒 | 额外独立验证秒 | report节点跨度秒 | 公开报告格式化毫秒 | HTML生成+写入秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 12.825 | 14.901 | 0.555858 | 1.139090 | 0.080 | 1.077644 |
| D1 | 13.421 | 46.875 | 1.547239 | 3.236563 | 0.184 | 2.870495 |
| D2 | 8.738 | 1.354 | 0.018782 | 0.142579 | 0.158 | 0.133527 |
| F1 | 12.574 | 46.072 | 1.557961 | 3.438390 | 0.187 | 2.868533 |
| G | 6.862 | 0.231 | 0.000734 | 0.018900 | 0.089 | 0.031639 |
| H | 7.444 | 0.373 | 0.002664 | 0.025444 | 0.118 | 0.056937 |

F2/E不进入这些节点。simulation_compute_seconds和mcp_direct_call_seconds保留null：当前MCP服务内部没有独立计时，不捏造分解。timings另保存verify_completed、收据load/parse、JSON/时间线写入、报告写入及HTML生成/写入分别计时。

B/D1/F1同一容量参数均出现45–47秒executor跨度，可复现的是该阶段总开销；还没有定位为引擎、传输或SDK问题。后续应先在MCP边界测量计算、序列化、输出Schema验证及重复解析/校验，再针对热点优化。这些记录没有单独的引擎计算计时。

## 报告修复与本地回归

G/H初始原生调用分别为run_simulation/run_server_simulation，参数、SimPy结果、独立验证和单次表格都正确；但公开context硬编码比较目标，报告尾注无条件引用配对样本。它属于**输出管道的文案缺陷**，不是LLM误选比较工具或模拟引擎错误。入口acceptance_passed只检查结构执行，漏掉这个语义错误；原始严格端到端率仍为6/8，不改初测结论。

报告按已验证的单次 / 比较结果类型生成目标和尾注，不复制模型自由文本或改变数值。
保存结果的本地报告重建不产生远程请求，**不计为真实 LLM 端到端复验**。
相关本地回归分别覆盖 213、280 个不同测试，均通过；不将重复运行相加，也不改变初测 6/8。
两次额外真实 G/H 请求的结论在下节单独列出。

UNSUPPORTED_REQUEST沿用旧兼容码，公开解释仅代表未获得受支持计划及能力边界，不能据这个码推断模型的具体动机。天气限制是当前注册能力事实；天气不是所有无工具响应的推定原因。原E/F2的原生拒绝正文依旧未保存、具体模型措辞未验收；旧F2仍为原UNSUPPORTED_REQUEST，不能把新的本地诊断追写到旧远程记录中。旧状态经新CLI公开投影可去重，但这是本地再展示，不是原CLI输出的补造。

上述无工具拒绝的JSON中scenario_id保持未确定，experiment_goal为null；公共State在未知场景时显示“实验请求尚未完成 / 未生成已验证的实验结果”，不默认标成便利店比较。普通CLI只输出一条终止拒绝说明，不输出成功报告；JSON和非JSON的失败退出码均1。配置失败仍退出2，暂停/成功与中断行为保留已有回归。

### 单次和比较报告复核

现有queue_report/server_report按已验证SimulationResult/ComparisonResult分支；单次无对照组、赢家、comparison_id或比较目标，报告数值只由typed evidence格式化。Queue展示顾客、收银台、平均等待和FIFO/指数假设；Server展示平均响应、nearest-rank P95、平均等待、完成数量、策略、节点和到达/工作量假设。否定性限制说明“不包含配对比较”不算伪造比较。

CLI 的公开报告从已验证收据重建，包含 --skip-visualization 路径；失败不回退到私有模型自由文本。

使用保存的真实 MCP 收据 SHA 加载 typed result，并调用 validate_tool_response；8/8 通过，结果 JSON 保持原字节。这是保存结果的本地复核，不增加真实模型请求。

| 保存真实结果的本地重建 | 复核结论 |
| --- | --- |
| A/B/C/D1/D2/F1六个比较结果 | 公开报告与原报告完全一致，原比较行为未退化 |
| G：20顾客、2收银台、单次 | 当前报告与上一轮另存修正版一致；平均等待13.401483秒（报告按原格式显示13.40），无比较组/赢家 |
| H：20请求、两台RR、单次 | 当前报告与上一轮另存修正版一致；平均响应0.055057秒，正确的服务器指标/假设，无比较组/赢家 |

## G/H 修复后真实复验

两例各一次真实请求，均返回 HTTP 200，
没有自动重试或重规划；用户输出由正式 CLI 共用的 task_output 格式器生成，
远程入口没有另外启动 CLI 子进程，本地 CLI 进程测试属于独立证据。

| 项目 | G | H |
| --- | --- | --- |
| 实际输入 | 用2个收银台模拟20位顾客排队。 | 用两台服务器和Round Robin策略模拟20个请求。 |
| 预期 / 原生实际工具 | run_simulation / run_simulation | run_server_simulation / run_server_simulation |
| 实际场景 | queue | server_load_balancing |
| 原生HTTP function调用 / AIMessage.tool_calls | 各1条，同名同参数投影，finish_reason=tool_calls | 各1条，同名同参数投影，finish_reason=tool_calls |
| 参数首次合法 | 是，规划信封及业务Schema有效，parameter_validator首次execute | 是，规划信封及业务Schema有效，parameter_validator首次execute |
| 请求 / 重规划 / 自动重试 | 1 / 0 / 0 | 1 / 0 / 0 |
| MCP实际执行 | 真实stdio，success，cached=false，1张SHA校验收据 | 真实stdio，success，cached=false，1张SHA校验收据 |
| 结果类型 | SimulationResult，单次 | ServerSimulationResult，单次 |
| 独立结果验证 | valid=true，sufficient=true，checked_calls=1；离线收据再复核通过 | valid=true，sufficient=true，checked_calls=1；离线收据再复核通过 |
| LangGraph最终状态 | completed，5个节点完成 | completed，5个节点完成 |
| 报告及用户可见输出 | 单次便利店等待时间；完整报告，stderr为空 | 单次服务器响应时间；完整报告，stderr为空 |
| 单次报告6项检查 | 6/6：类型、请求数量/资源、单次目标、无虚构比较、指标匹配、假设 | 6/6：另确认RR，其他同左 |
| 完整任务 / 失败阶段 | 通过 / 无 | 通过 / 无 |

G的校验后参数：20顾客、2收银台、共享FIFO，指数到达间隔均值10秒、指数服务时长均值25秒、seed=42、time_unit=second。平均等待 **13.401482981334386秒**，公开报告按既有格式显示13.40秒。

H的校验后参数：20请求、两台服务器，server_id分别1/2、各processing_rate=1 normalized_work/秒、capacity=1，routing_strategy=round_robin；指数到达间隔均值0.01秒、指数工作量均值0.025、独立FIFO、complete_then_dispatch、seed=42。平均响应 **0.05505711012301214秒**，报告显示0.055057秒，P95 0.136822秒、平均等待0.031047秒、完成20请求。

上述默认假设是复验模型实际生成并通过确定性校验的参数，不是验收入口补参或硬编码选择。两份新result.json恰与历史同参数/同种子的结果字节相同；这由本次真实执行产生，并非复用收据，cached=false。单次报告不出现对照组或赢家，明确有限实验、种子及模型边界，不将结果解释为生产性能或显著性证明。

两例节点序列均为 `planner:propose → parameter_validator:execute → executor:tool_success → result_validator:report → report:complete`，round=1。最终报告节点生成报告后完成，导出的公开报告再由已验证typed result经同一Report Generator生成；没有直接调用引擎绕过Agent。

响应 SHA：

- G：`79888264e5a444b3ccc11047b829579c4b56f9da25fce5125b939ecec72c8a87`。
- H：`4e6e4940e1d3425d204cce2f6882e9cfd8525d390b46860c185721f3771a8afc`。

保存结果经收据 SHA 校验和独立验证；新结果与历史同参数、同种子结果字节相同，
但来自新的实际工具执行，cached=false。旧审计、收据和结果未被覆盖。

| 案例 | 输入Token | 输出Token | 总Token | HTTP | 模型往返秒 | 案例整体秒 | 毛费用估算（元） |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| G | 6138 | 280 | 6418 | 200 | 6.355613 | 7.576203 | 0.0062544 |
| H | 6139 | 350 | 6489 | 200 | 7.554485 | 9.022325 | 0.0065912 |
| 复验合计 | **12277** | **630** | **12907** | 2个200 | **13.910098** | **16.598528**（案例耗时求和） | **0.0128456** |

整体秒为worker测得的单例全链路耗时；合计不包含父进程和两个worker之间的启动开销，不冒充整条命令墙钟耗时。供应商只返回Token usage，未返回可核查的独立服务端处理时间；“模型往返”是客户端HTTP计时。

| 阶段及计时性质（秒） | G | H |
| --- | ---: | ---: |
| workflow整体 | 6.543997 | 7.809462 |
| parameter_validator checkpoint跨度 | 0.004455 | 0.004500 |
| executor checkpoint跨度，含MCP/模拟/序列化/持久化 | 0.087724 | 0.119934 |
| result_validator checkpoint跨度 | 0.015352 | 0.013227 |
| report checkpoint跨度 | 0.010128 | 0.006683 |
| 末端收据独立复核 | 0.000219 | 0.000778 |
| 公开报告生成，不含写盘 | 0.000036 | 0.000042 |
| 既有HTML导出生成，不含写盘 | 0.007863 | 0.015924 |

原始精度和写盘/解析等计时保留在本地记录。MCP纯调用与引擎纯计算仍无单独计时，均为null，不能从executor跨度中捏造拆分数据，也不能用复验20人口的短耗时宣称已修复历史1000请求的性能问题。

| 统计口径 | 请求 | 输入 / 输出 / 总Token | 毛费用估算（元） | 严格报告任务通过 |
| --- | ---: | --- | ---: | --- |
| R1历史两轮，保留原结论 | 10 | 61437 / 4201 / 65638 | 0.0693144 | **6/8** |
| R1.1本地修复，不计真实请求 | 0 | 0 / 0 / 0 | 0 | 本地回归/保存结果重建，不能改变历史统计 |
| R1.1最终G/H真实复验 | 2 | 12277 / 630 / 12907 | 0.0128456 | **2/2** |
| 所有真实请求消费累计 | **12** | **73714 / 4831 / 78545** | **0.0821600** | 分轮报告，不将初测6/8追改8/8 |

实际账单、免费额度剩余和抵扣金额均未知；这里是客户端按官方原价估算，不声称实际扣款或可用余额。

## 工程测试与证据范围

保存的 R1 工程验收为 **633 passed，0 失败 / 错误 / 跳过**。
8 条警告来自 MCP 取消测试的 record_property 与 JUnit xunit2 格式兼容性。
后续报告修复和公开输出专项分别为 213、280 个不同相关测试，不把重复运行相加为新测试数。
相关验收通过 Ruff、格式检查及 strict mypy。后续保存的完整工程验收见
[Presentation 验收说明](presentation-mode-7.4.md)。

本地测试使用 MockTransport、NativeToolModel 或本机 HTTP 协议夹具；
MCP、模拟引擎、LangGraph、SQLite 和独立验证器实际执行。
这些测试证明协议、宿主约束及恢复行为，不能替代外部模型的语义证据。

## 限制

- 12 次请求包括两次拒绝和两次复验，不是 12 个不同成功任务，小样本不能证明任意自然语言可靠性。
- E/F2 拒绝解释原文缺失，完整拒绝语义未验证；本地客户端提示不能补作模型原文。
- 真实参数纠错、跨规划轮设计锁定、重规划和供应商失败分支没有被这些远程案例触发。
- Queue 与 Server 是有限假设模型，结果不代表现实商业决策、生产性能预测或统计显著性。
- executor 节点跨度包含 MCP、计算、序列化和持久化等开销；纯引擎及纯 MCP 计时未知。
- HTTP 耗时是客户端往返，Token 是供应商 usage，费用是历史价格估算，均不是独立服务端处理时间或实际账单。
