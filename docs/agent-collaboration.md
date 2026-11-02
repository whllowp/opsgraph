# 多 Agent 协作图

## 1. 为什么要建协作图

一个 Agent 往往不是单进程：主 Agent 会把日志分析、配置审查、测试修复委派给子 Agent。只看 session 列表会丢掉三件事：

- **责任链**：哪个子 Agent 的效果最终归属于哪个主 Agent；
- **成本**：子树总 token/成本，而不是单个会话；
- **风险**：子 Agent 的高风险动作是否会把主 Agent 的结论「污染」。

OpsGraph 把委派关系建成 UModel 自环：

```text
ai.agent_session --delegates_to--> ai.agent_session
```

## 2. 数据来源

AgentSight 的 session 本身没有强制的父子字段，因此适配层支持以下属性（按优先级）：

- `attributes.parent_session_id`
- `attributes.parent_session`
- `attributes.parent_session_key`

值可以是 session key，也可以是 `session_id` 别名。无法解析的父引用会被忽略，不会伪造边。

如果上游没有显式父字段，也可以从 `process_nodes` 的 `ppid/root_pid` 推断进程树；当前实现保留了这个扩展点，但默认只信显式 session 关系，避免把同一 PID 下的兄弟进程误判为父子。

## 3. 图字段

`ai.agent_session` 新增：

| 字段 | 含义 |
|---|---|
| `parent_session` | 父会话显示名 |
| `root_session` | 根会话显示名 |
| `depth` | 委派深度，根为 0 |
| `delegation_role` | `incident_commander` / `log_analysis` / `policy_review` / ... |
| `child_sessions` | 直接子会话数 |
| `subtree_tokens` | 自身 + 全部后代的 token |
| `subtree_cost_usd` | 子树成本 |
| `subtree_risk_score` | 子树最大风险分 |

`delegates_to` 边带 `role` 与 `tokens` 证据。

## 4. 聚合规则

```text
subtree_tokens(root) = tokens(root) + Σ tokens(descendants)
subtree_cost(root)   = cost(root)   + Σ cost(descendants)
subtree_risk(root)   = max(risk(root), max(risk(descendants)))
```

这样做的好处：

- 主 Agent 的「运行成本」不再被低估；
- 子 Agent 的高风险 effect 会抬升整个子树的风险；
- 控制台可以按树缩进展示，而不是按 token 排序把子会话混进根会话。

## 5. Demo

`agentsight_demo.py` 里：

- `sre-claude-01`（root，incident_commander）
  - `sre-log-analyst`（child，log_analysis，Claude Haiku）
  - `sre-policy-reviewer`（child，policy_review，GPT-5）
- `codex-console-02`（独立 root，independent_coder）

结果：

```text
4 sessions · 2 delegations · max depth 1
SRE subtree: 56,000 tokens / $0.22
```

控制台 Agent tab 中，子会话带 `↳` 缩进；主会话显示 `root session · 2 child(s) · subtree ...`。

## 6. 边界

- 不自动推断跨机器的 Agent 委派；必须有 session 级证据。
- 当前只做树，不做 DAG；一个子会话只有一个父。
- 环引用会被打断并降级为 root，不会导致死循环。
- 子树风险是 `max`，不是求和；它回答「最坏影响」，不是「总风险量」。
- 成本按 session 的 `model` 估算，未按实际账单校准。
