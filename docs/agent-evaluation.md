# Agent 历史基线与 Claim Accuracy

## 1. 两个问题

单次运行只能回答「这次做了什么」；运维还需要：

- **这次是否异常？** 和同一 Agent/模型的历史运行比，token、失败、重试环是否偏离。
- **这个 Agent 的结论可信么？** 历史上它声称的根因，有多少次与图 RCA 一致。

OpsGraph 用 `data/history/agent_history.json` 做离线基线，输出 `artifacts/baseline.json`。

## 2. 历史数据结构

```json
{
  "run_id": "run-2026-10-31-sre-05",
  "timestamp": "2026-10-31T10:07:00+08:00",
  "session_key": "sre-prev-05",
  "agent_type": "claude",
  "model": "claude-sonnet-4",
  "incident": "INC-2026-1031-02",
  "input_tokens": 26500,
  "output_tokens": 5500,
  "total_tokens": 32000,
  "cost_usd": 0.162,
  "duration_s": 880,
  "tool_calls": 11,
  "failed_tool_calls": 3,
  "retry_loops": 1,
  "risk_score": 74,
  "claim": "payment-gateway",
  "graph_root_cause": "checkout-service",
  "claim_correct": false
}
```

`claim_correct` 必须由历史图 RCA 回填，不能由 Agent 自评。

## 3. 基线指标

对每个**根会话**（`depth=0`），按 `agent_type` 聚合历史：

- `total_tokens`
- `cost_usd`
- `tool_calls`
- `failed_tool_calls`
- `retry_loops`
- `duration_s`

输出 `current / median / p95 / mean / samples / deviation_pct`。

偏离判定：

- token/cost/tools/duration：`current > max(p95*1.2, p95+1)`；
- 失败数/重试环：`current > p95 + 0.5`。

命中会生成 `agent-baseline-spike` finding。子会话不进入基线，但会计入父会话的 `subtree_tokens`。

## 4. Claim Accuracy

```text
accuracy(agent) = 历史 claim_correct 数 + 当前 verified 数
                  / 历史有 claim 的运行数 + 当前有 claim 的运行数
```

- `verified`：Agent 声称根因 == 图 RCA；
- `mismatch`：不一致；
- `unverifiable`：没有声明/图证据不足，**不计入分母**。

Demo：

```text
overall: 6/8 = 75%
claude:  5/6 = 83%
codex:   1/2 = 50%
```

当某 Agent 的样本 ≥3 且准确率 <70% 时，生成 `agent-claim-accuracy-low` finding。

## 5. CLI

```bash
python -m opsgraph baseline
python -m opsgraph baseline --history data/history/agent_history.json
```

查看 artifact：

```bash
cat artifacts/baseline.json
```

控制台 Agent tab 的「历史基线」区域显示：

- 当前 token / p95；
- 失败数 / p95；
- 重试环 / p95；
- 总体与分 Agent 的 claim accuracy。

## 6. 替换为真实历史

当前 `agent_history.json` 是合成的 8 条运行，用于离线演示。生产接入：

1. 每次 Agent 运行结束后，把根会话摘要写成一行（字段同上）；
2. 用当时的 UModel 图 RCA 回填 `graph_root_cause` 与 `claim_correct`；
3. 保留 `agent_type`/`model`/`repo`/`incident_type` 维度；
4. 按 30/90 天窗口滚动，避免旧行为污染基线；
5. 把 `history_path` 指向团队存储（SQLite/Postgres 导出均可）。

## 7. 边界

- 基线是统计，不是因果；token 上涨可能是任务变难。
- 小样本（<3）不生成低准确率 finding。
- claim accuracy 只衡量「根因判断」，不衡量修复质量；修复质量看 `remediation_status`。
- 当前历史是文件型，单人单机；生产要处理并发写入、去重与保留期。
