# 文档索引

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 五层架构、数据流、边界、失败模式、生产化路线 |
| [Graph Engineering](graph-engineering.md) | 方法论、证据边、观测/声明对账、RCA 公式、算子 |
| [AI Graph Engineering](ai-graph-engineering.md) | Agent 作为一等图节点、意图→决策→效果、AI 图算子 |
| [AgentSight 接入](agentsight.md) | snapshot/SQLite 适配、schema 映射、风险/重试环/校验 |
| [模型](model.md) | 16 EntitySet / 20 Link、字段、ID 规则、导入与查询 |
| [DeepFlow 接入](deepflow.md) | L7 SQL、tag 映射、实时模式、边界 |
| [Demo 导览](demo.md) | 场景时间线、控制台、CLI 复现、重置 |
| [Agent 接入](agent.md) | MCP、推荐提示词、查询、约束、Skill |

## 一分钟理解

1. DeepFlow 从 eBPF 采集 L7 调用，带 `pod_service`、`pod_node`、`request_domain`、`endpoint`。
2. OpsGraph 把这些事实编译成 UModel 的 16 类对象和 20 类关系，**每条边带指标证据**。
3. AgentSight 从 eBPF 采集 Agent 的 session/tool/file/network/process，编译成 `ai.*` 子图。
4. 图分析回答：谁是根因、影响哪些服务/业务、哪些边是影子依赖/僵尸依赖、Agent 是否在制造风险。
5. 控制台、报告、Agent 都读同一张 UModel 图。
