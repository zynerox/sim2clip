# Sim2Clip 文档

Sim2Clip 将自然语言问题转化为可复现、可独立核验的有限模拟实验。
首次使用可从项目 [Quick Start](../README.md#quick-start) 开始；
无需安装的保存结果演示见 [Demo 与离线包](release-demo.md)。

## 使用与设计

| 文档 | 内容 |
| --- | --- |
| [CLI](cli.md) | 安装、模型配置、固定 Demo、暂停和恢复 |
| [Agent Workflow](agent-workflow.md) | 五节点流程、条件反馈、证据与 SQLite checkpoint |
| [MCP Server](mcp-server.md) | 四工具、stdio 协议、类型契约及错误处理 |
| [多场景编排](multi-scenario-agent-m3.md) | Registry、设计锁定及旧状态兼容 |
| [Queue 契约](simulation-contract.md) | 参数、共享 FIFO、配对实验及指标 |
| [Server 模型](server-load-balancing-m1.md) | 独立队列、路由策略、调度和手算规范 |
| [Server Engine](server-load-balancing-m2.md) | 计算接口、负载来源及独立验证 |
| [Visualization](visualization.md) | 保存事件回放及离线导出 |
| [Presentation Mode](presentation-mode-7.4.md) | 六阶段叙事、展示时钟、执行证据及揭晓 |
| [9:16 舞台](visualization-vertical-7.3.md) | Queue / Server 竖屏构图与聚合 |

## 验收与发布

- [真实模型验收](real-llm-r1-verification.md)：Qwen3.5-Plus，累计 12 次请求；
  原始完整任务 6/8，修复后 G/H 复验 2/2，拒绝解释和重规划的未验证范围明确标注。
- [本地兼容性验收](multi-scenario-m5-verification.md)：协议夹具、恢复、独立验证及浏览器检查。
- [v1.0.0 发布说明](release-notes-v1.0.0.md)：版本能力、公开附件及限制。
- [Demo 与离线包](release-demo.md)：媒体规格、来源、SHA256、观看与录制方法。

验收报告中的来源标识和测试数字对应已保存记录，不表示每次文档更新均重新执行。
演示中的 30 秒是回放展示时间，有限模型结果不代表现实预测或统计显著性。

模型设计文档定义契约与假设；验收报告说明实际验证范围。
保存的测试记录不代表所有操作系统、运行版本或任意自然语言输入均已验证。
