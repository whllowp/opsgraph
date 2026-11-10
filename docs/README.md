# 文档索引

| 文档 | 内容 |
|---|---|
| [架构](architecture.md) | 五层架构、数据流、边界、失败模式、生产化路线 |
| [Graph Engineering](graph-engineering.md) | 方法论、证据边、观测/声明对账、RCA 公式、算子 |
| [AI Graph Engineering](ai-graph-engineering.md) | Agent 作为一等图节点、意图→决策→效果、AI 图算子 |
| [多 Agent 协作](agent-collaboration.md) | parent/child session、子树 token/成本/风险、delegates_to |
| [执行前策略门禁](policy-gate.md) | allow/require_approval/deny、图上下文、CLI/API、接入 Agent Runtime |
| [Agent 基线与评估](agent-evaluation.md) | 历史基线、token/loop 偏离、claim accuracy |
| [代码级 RCA / REA](code-rca.md) | eBPF 代码栈、行级 RCA、伪代码、10 次修复闭环 |
| [值班决策简报](brief.md) | 从图证据到行动：owner、依据、验收、不要做、数据来源 |
| [智能监控告警](alerts.md) | 策略配置、去重/静默/分组/抑制、告警组自动 RCA |
| [AgentSight 接入](agentsight.md) | snapshot/SQLite 适配、schema 映射、风险/重试环/校验 |
| [模型](model.md) | 22 EntitySet / 29 Link、字段、ID 规则、导入与查询 |
| [DeepFlow 接入](deepflow.md) | L7 SQL、tag 映射、实时模式、边界 |
| [Demo 导览](demo.md) | 场景时间线、控制台、CLI 复现、重置 |
| [Agent 接入](agent.md) | MCP、推荐提示词、查询、约束、Skill |

## 一分钟理解

1. DeepFlow 从 eBPF 采集 L7 调用，带 `pod_service`、`pod_node`、`request_domain`、`endpoint`。
2. OpsGraph 把这些事实编译成 UModel 的 22 类对象和 29 类关系，**每条边带指标证据**。
3. AgentSight 从 eBPF 采集 Agent 的 session/tool/file/network/process，编译成 `ai.*` 子图，包括 parent/child 委派。
4. 图分析回答：谁是根因、影响哪些服务/业务、哪些边是影子依赖/僵尸依赖、Agent 是否在制造风险、子 Agent 成本归属。
5. 执行前 gate 用图上下文做 allow/approval/deny；历史基线与 claim accuracy 回答「这次是否异常、这个 Agent 是否可信」。
6. 代码级 RCA 把 eBPF 用户态栈映射到 `file:line`，REA 用伪代码 → 补丁 → 金融回归 → eBPF 复测完成 10 次闭环。
7. 控制台、报告、Agent 都读同一张 UModel 图。
