# 值班决策简报

> 产品原则：值班的人不需要第十个仪表盘，需要一句「现在做什么、谁来做、做完看什么」。

![值班决策简报](brief-tab.png)

## 为什么做

OpsGraph 已经有服务图、Agent 图、代码 RCA、门禁和历史基线。但信号越多，on-call 越容易停在「图很漂亮，下一步呢」。

决策简报把已有证据压缩成一页：

1. **发生了什么**：受害服务、P99/基线/错误率、QPS；
2. **为什么**：根因、置信度、Top 理由；
3. **AI 做了什么**：高风险 session、系统效果、claim 是否一致、处置是否待验证；
4. **下一步**：带 owner、依据、验收标准的 P0/P1 动作；
5. **不要做**：命中 deny 的破坏性动作；
6. **证据来源**：哪些是 replay、哪些是真实 perf、UModel 是否在线。

它不是新的事实源，只是对 `snapshot + analysis + agents + policy + alerts + code_rca + code_graph` 的确定性编排。

简报顶部同时显示告警压缩摘要（如 `206 events -> 1 group / 99.5%`），避免值班的人再打开一个告警系统。

## 动作生成逻辑

| 条件 | 生成的行动 |
|---|---|
| Agent `remediation_status=pending_validation` | P0 验证 SLO 是否真正恢复 |
| 存在高风险系统效果 | P1 复核变更并补齐审批 |
| 门禁 `require_approval` | P0 创建审批单，通过后再执行 |
| 有代码级修复 | P1 纳入发布计划，跑 canary + 回滚包 |
| 以上都没有 | P0 单变量验证根因 |

每条动作都带：

```text
priority / action / owner / owner_source / because / verify / check
```

其中：

- `because` 必须能回到图、Agent 或代码事实；
- `verify` 是给人看的验收描述；
- `check` 是机器可执行的检查，决定 action 能否自动关闭。

### 可执行检查

示例：

```json
{
  "kind": "slo",
  "service": "payment-gateway",
  "metric": "latency_p99_ms",
  "operator": "<=",
  "target": 800
}
```

- 当前 P99 2255ms → `check.failed`，动作保持 `open`；
- 新窗口 P99 700ms → `check.passed`，动作自动 `closed`；
- 之后再次恶化到 900ms → 动作自动 `reopened`。

`kind=approval` 通过后动作是 `unblocked`，不是 `closed`——审批只解除阻塞，执行完成仍需人工确认。

### CODEOWNERS

代码动作的 owner 来自仓库根目录 `CODEOWNERS`：

```text
* @opsgraph-core
/demo/fintech/ @commerce-core
/demo/fintech_c/ @commerce-core
/opsgraph/ @opsgraph-core
```

所以 `demo/fintech/payments.py` 的修复 owner 是 `@commerce-core`，不是服务 owner。

### 审批单

`require_approval` 不再只是提示，`brief` 会创建持久化审批单：

```bash
python -m opsgraph brief
python -m opsgraph approvals
python -m opsgraph approvals --decide APR-308d652d3f --decision approved --actor cto
```

审批单保存在 `artifacts/approvals.json`，包含 target、rule、approvers、created_at、decided_by。控制台 `/api/approvals` 可读。

### 审计链

每次动作状态变化写入 `artifacts/brief_history.jsonl`：

```text
opened → verification failed → closed / reopened
```

事件包含 `action_id`、owner、check 结果、时间。控制台简报底部显示最近 AUDIT，`brief.md` 有完整「审计链」章节。

## 使用

```bash
python -m opsgraph brief
```

生成：

```text
artifacts/brief.json
artifacts/brief.md
artifacts/approvals.json
artifacts/brief_history.jsonl
```

控制台：

- 诊断 tab 顶部直接显示；
- 顶栏「决策简报」打开 `/api/brief`；
- `GET /api/brief` 返回 Markdown；
- `GET /api/approvals` 返回审批单。

`run-demo.ps1` 会在 `code-demo` 之后自动生成简报。

## 示例

```text
Payment P99 SLO breach：根因 checkout-service（78%），受害 payment-gateway。

[P0] 验证 Agent 对 checkout-service 的处置是否真正恢复了 SLO
     owner=payments-backend
     check=slo payment-gateway latency_p99_ms <= 800 -> failed

[P0] 审批单 APR-308d652d3f：kubectl apply retry-policy 通过后再执行
     owner=commerce-core, change-manager
     check=approval -> pending

[P1] 复核 Agent 的高风险变更并补齐审批记录
[P1] 把 10 个代码级修复纳入发布计划
     owner=@commerce-core   # CODEOWNERS
```

## 硬门禁

CI 的 `real-perf` job 已移除 `continue-on-error`，真实 perf 10 次 C REA 失败会直接让流水线失败。


## 边界

- 简报只读已物化 artifacts，不重新跑 RCA；`/api/state` 会实时从当前图状态重建。
- `provenance` 明确标注 replay / real perf；不会把回放说成生产实时。
- 动作是建议，不是自动执行；真正执行仍走 policy gate 和人工审批。
- `approval` 通过只让动作 `unblocked`；不会把“审批通过”误记为“执行完成”。
- 代码动作 owner 来自 `CODEOWNERS`；没有匹配时回退到根因服务 owner。
