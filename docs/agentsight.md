# AgentSight 接入

[AgentSight](https://github.com/eunomia-bpf/agentsight) 是 eunomia-bpf 社区的系统级 AI Agent 观测工具：用 eBPF 挂 `SSL_read`/`SSL_write`，同时追踪进程、文件、网络与资源，不需要 Agent SDK 或代理。

OpsGraph 把 AgentSight 的观测编译成 UModel 的 AI 域对象，使 Agent 与服务、事件、变更出现在同一张可查询的图里。

## 1. 数据来源

支持两种公开产物：

| 来源 | 命令 | OpsGraph 读取 |
|---|---|---|
| JSON snapshot（schema v1） | `agentsight report export -o snapshot.json` | `opsgraph demo --agentsight snapshot.json` |
| SQLite 录制库 | `agentsight-*.db` | `opsgraph demo --agentsight agentsight-*.db` |

JSON 走 `load_snapshot()`；SQLite 走 `load_sqlite()`，读取 `llm_calls`、`token_usage`、`tool_calls`、`audit_events`、`process_nodes`、`network_targets`、`resource_samples` 表。两者最终归一到同一个 `AgentSnapshot`。

## 2. Schema 映射

| AgentSight 字段 | OpsGraph 落点 |
|---|---|
| `sessions[].id` / `attributes.session_id` | `ai.agent_session.session_key` |
| `sessions[].agent_type` / `model` / `status` | `ai.agent_session` 属性 |
| `sessions[].input_tokens` / `output_tokens` / `total_tokens` | Session token 与成本 |
| `sessions[].attributes.cwd` / `prompt_preview` | Session 上下文（截断） |
| `sessions[].attributes.linked_service` | `ai.agent_session --acts_on--> svc.service` |
| `sessions[].attributes.incident_id` | `ai.agent_session --investigates--> ops.incident` |
| `sessions[].attributes.claimed_root_cause` | `claim_status` 校验 |
| `token_summary[]` | `ai.model`（tokens / calls / cost） |
| `tool_calls[]` | `ai.tool_call`（工具、输入摘要、状态、时长、重试） |
| `tool_calls[].related_pid` | 与 `process_nodes` 关联 |
| `audit_events[]` | `ai.effect`（file/process/network/llm） |
| `audit_events[].details.risk` | 显式风险等级（可选） |
| `process_nodes[]` | pid → session 归属；命令效果 |
| `network_targets[]` | 网络效果（SQLite 模式下） |
| `resource_samples[]` | Session `cpu_peak` / `rss_peak`（内部指标） |

`ai.tool_call --produces--> ai.effect` 的匹配规则：

1. 同一 session；
2. target/command 文本重叠；
3. 时间差 ≤ 2 分钟；
4. 取分数最高且时间最近的一条。

匹配不上的 effect 仍然进入图，只是没有 tool 父节点——这比静默丢弃更诚实。

## 3. 图模型

```text
ai.agent_session --uses_model----> ai.model
ai.agent_session --invokes-------> ai.tool_call --produces--> ai.effect
ai.agent_session --acts_on-------> svc.service
ai.agent_session --investigates--> ops.incident
ai.effect        --affects-------> svc.service
```

关键字段：

- `ai.agent_session`：`total_tokens`、`cost_usd`、`tool_calls`、`failed_tool_calls`、`retry_loops`、`risk_score`、`friction_score`、`risk_level`、`claim_status`、`remediation_status`。
- `ai.tool_call`：`tool_name`、`status`、`target`、`command`、`retry_index`、`retry_of`、`risk`。
- `ai.effect`：`effect_type`、`action`、`target`、`status`、`risk`、`confidence`。

## 4. 分析规则

### 风险

| 规则 | 风险 |
|---|---|
| `kubectl apply/delete/rollout`、`rm -rf`、`terraform apply` | high |
| 写 `/prod`、`secrets`、`.env`、retry/config 文件 | high |
| 连接 `kubernetes.default` / prod 管理面 | high |
| 失败的工具/进程 | medium |
| 文件写、普通进程、普通网络 | medium/low |

Session 风险分 = Σ effect 权重 + 失败工具 ×4 + 重试环 ×8 + claim mismatch ×15，封顶 100。

### 重试环

同一 session 内，`(tool_name, target)` 出现 ≥2 次，且满足以下任一条件：

- 至少一次失败；
- 两次尝试间隔 ≤10 分钟。

这与服务侧的 retry storm 是同一类图模式，只是发生在 Agent 的 tool 层。

### 结论校验

- `claim_root_cause` 与 `analysis.root_cause` 相等 → `verified`；
- 不等 → `mismatch`，session 风险 +15；
- 没有声明 → `unverifiable`。

### 处置校验

- `attributes.remediation_target` 指向的服务仍为 `critical/warning` → `pending_validation`；
- 服务已恢复 → `applied`。

这避免「Agent 说修好了」被当成事实。

## 5. 录制与导出

在 Linux 上：

```bash
# 录制一个 Agent
sudo agentsight record -- claude

# 导出快照
agentsight report export -o agentsight_snapshot.json

# 或直接用 SQLite
ls agentsight-*.db
```

Windows/macOS 上可以用 AgentSight 的 agent-native session 路径（`agentsight top` / `report --local`）生成会话；OpsGraph 消费的是导出 JSON 或 DB，不依赖 eBPF 是否可用。

## 6. 隐私

AgentSight 快照可能包含：

- prompt / response 文本；
- 文件路径、命令、cwd；
- 网络 host/path；
- 工具输入输出。

OpsGraph 只把**摘要与派生指标**写入 UModel：

- `prompt_preview` 截断 240 字符；
- tool `input_summary` / `output_summary` 截断；
- effect 只保留 target + summary；
- 原始 `snapshot.json` 不进入 Git。

生产环境应在采集端做脱敏，并按 AgentSight 的 `snapshot-schema.md` 建议处理访问控制与保留期。

## 7. 当前 Demo

`agentsight_demo.py` 生成两个会话：

- `sre-claude-01`：SRE Agent 排查 payment 故障，写入 retry 配置、`kubectl apply`、rollout、验证 SLO；
- `codex-console-02`：编码 Agent 修改 OpsGraph 控制台，测试失败后修复。

生成物：`data/replay/agentsight_snapshot.json`。

## 8. 限制

- `tool_calls` 与 `audit_events` 的匹配是启发式的；不同 Agent 的工具命名差异很大。
- 成本是估算：默认价目表在 `agent_graph.py`，应按实际合同价替换。
- 没有把 Agent 的 prompt/response 内容图化；这是隐私选择，也意味着语义级判断需要额外的脱敏摘要。
- 目前不做多 Agent 协作图（parent/child session）；AgentSight 的 session 树可以作为下一步。
