# AI 时代的 Graph Engineering

## 1. 从「服务图」到「行为图」

传统运维图回答：服务之间怎么调、谁影响谁、哪里慢。

AI 时代还需要回答：

- **谁在改变系统？** 不只是 CI/CD，还有会执行命令、改文件、调 API 的 Agent。
- **它为什么这么做？** 从 prompt 到 model call 到 tool decision 的意图链。
- **它实际做了什么？** 从 tool call 到 file/process/network effect 的证据链。
- **它的结论可信吗？** Agent 声称的根因/修复，是否与 eBPF/UModel 图一致。
- **代价是什么？** token、成本、重试、延迟、风险。

AgentSight 提供「行为事实」，UModel 提供「语义契约」，OpsGraph 提供「图算子」。三者合起来，才是 AI 时代的 Graph Engineering。

## 2. 四层图

```text
意图层  prompt / plan / claimed root cause
  │
决策层  model call / tool call / retry
  │
效果层  file / process / network / LLM
  │
对象层  service / endpoint / incident / change / node
```

OpsGraph 的 AI 域实体正是按这四层建模：

- `ai.agent_session`：意图与运行上下文；
- `ai.tool_call`：决策；
- `ai.effect`：内核观测到的效果；
- `svc.service` / `ops.incident`：被作用的对象。

## 3. 五个 AI 图算子

### 3.1 Agent 爆炸半径

从 session 出发，沿 `produces`/`affects` 收集：

- 读/写了哪些文件；
- 执行了哪些命令；
- 连接了哪些 host；
- 落到哪些服务/事件。

与「摘除服务」的 blast radius 不同，Agent 的爆炸半径是**行为半径**：它不一定让服务宕机，但可能改配置、触发 rollout、写入错误文件。

### 3.2 重试环

服务侧的重试环是 `client -> server` 的流量放大；Agent 侧的重试环是 `session -> tool` 的重复失败。

两者共享同一个图模式：

```text
同一个目标 + 连续失败 + 时间窗内重复
```

OpsGraph 用 `(session, tool, target)` 聚合，输出 attempts/failures/duration。

### 3.3 成本归因

Token 与成本沿 `ai.agent_session --uses_model--> ai.model` 归因：

- 单次事故排查花了多少 token；
- 哪个模型/Agent 最贵；
- 哪个 tool 失败最多、最烧 token；
- 与 incident 关联后，可以算「一次故障的 AI 处置成本」。

### 3.4 结论校验（Graph-grounded evaluation）

Agent 的 `claimed_root_cause` 不是事实。OpsGraph 把它与 UModel 图 RCA 的 `root_cause` 对比：

- `verified`：一致；
- `mismatch`：冲突，风险 +15；
- `unverifiable`：没有声明或图证据不足。

这比让另一个 LLM 打分更可复现：比较的是两个可查询字段，而不是两段自然语言。

### 3.5 处置校验

Agent 执行了 `kubectl apply` / rollout，并不等于故障恢复。OpsGraph 检查目标服务在同一窗口的 eBPF 指标：

- 仍 `critical/warning` → `pending_validation`；
- 已恢复 → `applied`。

这把「Agent 说修好了」变成「图说还没好」。

## 4. 为什么 Agent 必须是一等节点

如果 Agent 只存在于日志里：

- 无法沿图查询「哪些 Agent 碰过这个服务」；
- 无法把 token 成本归因到事故；
- 无法把 tool 失败与服务异常对齐；
- 无法让下一个 Agent 复用上一次的结论与风险。

一旦 Agent 成为 UModel 节点：

- 服务 RCA 可以 join Agent 行为；
- Agent 可以查询自己上一次的运行图；
- 控制台可以在同一张图里显示「服务异常 + Agent 处置」；
- 多 Agent 协作建模为 session 之间的 `delegates_to` 边；父会话汇总子树 token/成本/风险。

## 5. 可信度与边界

- **证据分层**：`evidence=agentsight.snapshot` / `agentsight.sqlite`，与 `deepflow.l7_flow_log` 区分。
- **置信度**：AgentSight 的 `confidence` 原样保留；缺失时默认 0.9，不假装精确。
- **内容最小化**：只图化摘要和派生指标，不图化 prompt/response 全文。
- **人类在环**：高风险 effect 只生成 finding 与建议，不自动执行回滚。
- **可解释**：每个 session 的 risk/friction/claim/remediation 都有规则与字段，不是黑盒分数。

## 6. Demo 读法

打开控制台，右侧 `Agent` tab：

1. 两个 session：SRE Agent（high risk，claim verified，remediation pending）与编码 Agent（low risk）。
2. 点 SRE session：看到 `apply_patch` 2 次失败后成功、`kubectl_apply` 2 次 forbidden 后成功、`verify_slo` 仍 breaching。
3. 图上 AI lane：session 通过 `acts_on` 连到 `checkout-service`。
4. `Agent 发现`：重试环、高风险效果、claim verified、remediation pending。
5. 右侧 `查询`：直接跑 `.entity with(domain='ai', name='ai.agent_session')`。

## 7. 已落地的三个扩展

- **多 Agent 协作**：`parent_session_id` → `delegates_to`，`depth/child_sessions/subtree_tokens/subtree_cost/subtree_risk`；详见 [多 Agent 协作](agent-collaboration.md)。
- **执行前门禁**：`policy.py` 读取图上下文，返回 allow/require_approval/deny；详见 [执行前策略门禁](policy-gate.md)。
- **历史基线 + claim accuracy**：`history.py` 对比同 Agent 历史 p95，并统计 claim 与图 RCA 的一致率；详见 [Agent 基线与评估](agent-evaluation.md)。

## 8. 下一步

- 多 Agent DAG 与跨机器委派（`observes`、`handoff`）。
- 把 gate 接到 tool proxy / MCP server / admission controller，形成强制拦截。
- 用更多真实运行替换合成历史，加入 repo/incident 维度。
- Agent 记忆：把 verified claim、remediation、runbook 回写成可检索的图上下文。
- 评估集：claim accuracy、risk precision、cost per incident 的持续回归。
