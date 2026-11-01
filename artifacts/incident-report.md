# 故障报告：Payment P99 SLO breach

- 事件：`Payment P99 SLO breach`（P1，investigating）
- 受害服务：`payment-gateway`
- 根因：`checkout-service`（置信度 78%）
- 时间窗口：2026-11-02T00:00:00+08:00 → 2026-11-02T01:00:00+08:00
- 图规模：119 个实体 / 183 条关系

## 结论

Root cause is checkout-service, upstream of the victim payment-gateway. Score 0.96 (confidence 78%). high-risk config at 00:10 touching retry/timeout settings; outbound 4.6x baseline.

因果路径：`checkout-service → payment-gateway → payment-router → Alipay Gateway`

## 根因排序

| # | 服务 | 得分 | 自身异常 | 变更关联 | 出向放大 | 因果领先 | 图位置 | 关键证据 |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `checkout-service` | 0.96 | 0.132 | 0.520 | 0.061 | 0.016 | 0.200 | P99 1952ms, anomaly 17.1；high-risk config at 00:10 touching retry/timeout settings；outbound 4.6x baseline |
| 2 | `payment-gateway` | 0.47 | 0.198 | 0.010 | 0.120 | 0.016 | 0.100 | P99 2255ms, anomaly 25.7；recent deployment is low risk；outbound 8.0x baseline |
| 3 | `payment-router` | 0.41 | 0.280 | 0.000 | 0.117 | 0.016 | 0.000 | P99 1983ms, anomaly 36.4；no recent change；outbound 7.8x baseline |
| 4 | `frontend-web` | 0.28 | 0.028 | 0.000 | 0.040 | 0.000 | 0.180 | P99 440ms, anomaly 3.6；no recent change；outbound 3.3x baseline |
| 5 | `risk-control` | 0.23 | 0.099 | 0.000 | 0.120 | 0.016 | 0.000 | P99 618ms, anomaly 12.9；no recent change；outbound 8.3x baseline |
| 6 | `ledger-service` | 0.19 | 0.056 | 0.000 | 0.115 | 0.016 | 0.000 | P99 478ms, anomaly 7.3；no recent change；outbound 7.7x baseline |

## 时间线

| 时间 | 类型 | 事件 | 说明 |
|---|---|---|---|
| 23:50 | change | payment-gateway v3.2.1 deployed | Logging format only (red herring). |
| 00:05 | business | Flash Sale campaign active | Traffic multiplier 3.2x begins to ramp. |
| 00:10 | change | checkout retry policy changed | max_retries 2 -> 5, timeout 500ms -> 2000ms. |
| 00:10 | signal | checkout-service outbound amplification onset | Outbound 4.6x baseline, before the latency breach. |
| 00:10 | signal | payment-gateway outbound amplification onset | Outbound 8.0x baseline, before the latency breach. |
| 00:13 | signal | checkout-service p99 onset | P99 1952ms, 1343% vs baseline. |
| 00:13 | signal | payment-gateway p99 onset | P99 2255ms, 1915% vs baseline. |
| 00:13 | signal | payment-router p99 onset | P99 1983ms, 2036% vs baseline. |
| 00:13 | signal | risk-control p99 onset | P99 618ms, 1954% vs baseline. |
| 00:20 | noise | node-3 CPU saturation | batch-reporter saturates node-3 (not on the payment path). |
| 00:25 | alert | node-3 CPU saturation 92% | Sustained CPU pressure on node-3 after the nightly batch reporter started. |
| 00:30 | business | Flash Sale traffic peak | 3.2x traffic meets 2.5x retry amplification. |
| 00:40 | alert | SLO burn-rate alert fires | payment-gateway P99 2.6s, burn rate 14.2x. |
| 00:42 | incident | INC-2026-1102-01 opened | Payment on-call starts graph investigation. |

## 影响面

- 9 services and 3 business journeys are in the blast radius; peak exposure is ~5166 qps.
- 受影响服务：cart-service, checkout-service, inventory-service, ledger-service, notification-service, order-service, payment-gateway, payment-router, risk-control
- 业务旅程 `Standard Checkout`（platinum）：18% drop in paid orders，营收敞口 CNY 2.4M/hour
- 业务旅程 `Subscription Renewal`（gold）：6% renewal failure，营收敞口 CNY 380k/hour
- 业务旅程 `Browse & Cart`（silver）：p95 degraded by 340ms，营收敞口 CNY 120k/hour

## 图工程发现

- **[critical] Retry amplification on the root-cause edge**：checkout-service -> payment-gateway: 3335 rps vs 112ms baseline P99. Outbound amplification is 4.6x while the flash-sale multiplier is only 3.2x.
- **[warning] Observed dependency is missing from the declared model**：checkout-service calls promo-flash.com API (api.promo-flash.com) at 127 rps, P99 959ms, error 3.9%. It was discovered by eBPF but is absent from the CMDB.
- **[warning] Service graph has articulation points**：Removing ledger-service, payment-gateway disconnects part of the call graph. These are structural single points of failure.
- **[info] Declared dependency has no observed traffic**：order-service -> notification-service is declared but produced zero L7 requests in the window. Candidate for model cleanup or a broken integration.
- **[info] node-3 resource pressure**：CPU 92% (z=38.4). No payment-gateway pods on this node. Hosted services: batch-reporter, inventory-service, order-service, risk-control.

## 已排除

- **payment-gateway v3.2.1 is low risk**：logging format only; no request-path change Applied at 2026-11-01T23:50:00+08:00. No request-path change.
- **node-3 pressure is not on the victim path**：CPU 92% but payment-gateway has no pods on this node. Hosted services: batch-reporter, inventory-service, order-service, risk-control.
- **Flash Sale explains baseline traffic, not the retry multiplier**：All services see ~3.2x traffic after 00:30, but checkout -> payment-gateway grows ~8x. The extra ~2.5x is retry amplification.

## AI Agent 观测（AgentSight）

- 会话：2；工具调用：16；失败：5；重试环：3
- Token：69,400；成本：$0.27；高风险效果：4

| Agent 会话 | 模型 | Tokens | 成本 | 工具/失败 | 重试环 | 风险 | 关联服务 | 结论校验 |
|---|---|---:|---:|---:|---:|---|---|---|
| `claude:sre-claude-01` | claude-sonnet-4 | 44,600 | $0.21 | 11/4 | 2 | high | checkout-service | verified |
| `codex:codex-console-02` | gpt-5 | 24,800 | $0.06 | 5/1 | 1 | low | - | unverifiable |

- **[critical] apply_patch retry loop**：sre-claude-01 attempted apply_patch 3 times against /workspace/opsgraph/checkout-retry-policy.yaml (2 failures) over 60s.
- **[critical] kubectl_apply retry loop**：sre-claude-01 attempted kubectl_apply 3 times against kubectl apply -f checkout-retry-policy.yaml -n shop (2 failures) over 120s.
- **[warning] exec retry loop**：codex-console-02 attempted exec 2 times against npm test -- --runInBand (1 failures) over 180s.
- **[critical] Agent touched high-risk system surfaces**：4 high-risk effects, including write /workspace/opsgraph/checkout-retry-policy.yaml.
- **[info] Agent root cause verified against graph**：claude:sre-claude-01 claimed checkout-service; graph RCA says checkout-service (verified).
- **[warning] Remediation is not yet validated by eBPF**：claude:sre-claude-01 changed checkout-service, but the service is still critical in the same window.

## 复现查询

```bash
umctl --addr http://127.0.0.1:18080 query run opsgraph \
  ".entity with(domain='svc', name='svc.service', query='checkout-service')"

umctl --addr http://127.0.0.1:18080 query run opsgraph \
  ".topo | graph-call getDirectRelations([(:\"svc@svc.service\" {__entity_id__: '<checkout-id>'})])"
```

模型、实体与关系均通过 UModel 公共 REST/CLI 契约写入和读取；`calls` 边携带 eBPF 证据，`ai.*` 边携带 AgentSight 证据。
