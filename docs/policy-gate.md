# 执行前策略门禁

## 1. 为什么需要「执行前」

事后 finding 只能告诉你「Agent 已经改了 prod」。执行前门禁要在 tool call 落到系统之前回答：

- 这个动作会碰到哪个服务？
- 这个服务现在是不是事故受害/根因？
- Agent 当前风险分、claim 状态是否可信？
- 应该 allow、require_approval，还是 deny？

OpsGraph 的 gate 不执行动作，只返回决策。它适合被 Agent runtime、Tool Proxy、MCP server 或人机审批流调用。

## 2. 决策级别

| 决策 | 含义 |
|---|---|
| `allow` | 放行，保留审计 |
| `require_approval` | 必须由指定审批人确认后才能执行 |
| `deny` | 拒绝，不允许自动执行 |

## 3. 规则（按优先级）

| 规则 | 条件 | 结果 |
|---|---|---|
| `deny-destructive-action` | `kubectl delete`、`rm -rf`、`terraform destroy` 等 | deny |
| `approve-prod-write-during-incident` | 写操作 + prod/关键服务 + 事故仍活跃 | require_approval |
| `approve-prod-write` | 写操作 + prod/关键服务 | require_approval |
| `approve-high-risk-write` | 写操作或高风险 tool | require_approval |
| `allow-read-only` | `query_umodel`、`read_file`、`kubectl_get` 等 | allow |
| `approve-high-risk-agent` | session risk ≥60 且动作有状态变更 | require_approval |
| `allow-default` | 未命中任何阻断规则 | allow，继续监控 |

「prod/关键服务」的判定同时看：

- target/command 中的 `prod`、`production`、`payments`、`shop`；
- 目标服务 `criticality=platinum/gold`；
- 目标服务 `status=critical/warning`；
- 事故 `status=investigating/open/mitigating`。

## 4. 图上下文

Gate 读取的是物化后的 UModel 快照，而不是自然语言：

```json
{
  "service": "payment-gateway",
  "service_status": "critical",
  "criticality": "platinum",
  "role": "victim",
  "incident_active": true,
  "agent_risk": 100,
  "claim_status": "verified"
}
```

同一个 `kubectl apply`：

- 在 sandbox、无事故、低风险 Agent → allow；
- 在 prod、事故活跃、高风险 Agent → require_approval；
- 如果是 delete → deny。

## 5. CLI

```bash
python -m opsgraph gate \
  --session sre-claude-01 \
  --tool kubectl_delete \
  --target "kubectl delete deploy/payment-gateway -n payments"
```

输出：

```json
{
  "decision": "deny",
  "rule": "deny-destructive-action",
  "risk": "high",
  "service": "payment-gateway",
  "reasons": ["action is destructive and cannot run unattended", "..."],
  "approvers": []
}
```

## 6. HTTP API

```http
POST /api/gate
Content-Type: application/json

{
  "session": "sre-claude-01",
  "tool": "kubectl_apply",
  "target": "kubectl apply -f checkout-retry-policy.yaml -n shop",
  "command": "..."
}
```

控制台 Agent tab 有「执行前门禁」面板，预置三个动作，点击即可看到决策、规则、审批人和图证据。

## 7. 接入 Agent Runtime

伪代码：

```python
decision = requests.post(gate_url, json={
    "session": session_id,
    "tool": tool_name,
    "target": target,
    "command": command,
}).json()

if decision["decision"] == "deny":
    raise PermissionError(decision["reasons"])
if decision["decision"] == "require_approval":
    approval = request_human_approval(decision["approvers"], decision)
    if not approval:
        raise PermissionError("approval denied")
run_tool(...)
```

## 8. 边界

- 当前 gate 是**建议性**的：OpsGraph 不劫持 Agent 的系统调用。要强制执行，必须把它接到 tool proxy / eBPF LSM / admission controller。
- 规则是白名单 + 破坏性命令启发式，不是完整 K8s RBAC；生产应叠加 OPA/Kyverno。
- `require_approval` 的审批人来自服务 owner 与固定角色；真实组织应接 IAM/on-call。
- 目前不评估「动作的执行结果」，只评估「动作前的意图与目标」。
- 高风险判定依赖 target/command 文本；Agent 混淆命令时需要额外的参数级解析。
