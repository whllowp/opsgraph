# 智能告警报告

- 输入事件：246
- 去重后：50
- 告警组：2
- 去重：183 · 抑制：27 · 静默：13
- 压缩率：99.2%

## 策略

| 策略 | 指标 | 阈值 | 级别 | 自动 RCA |
|---|---|---:|---|---|
| SLO burn rate | `slo_burn_rate` | 2.0 | P1 | yes |
| P99 latency vs baseline | `latency_ratio` | 2.0 | P2 | yes |
| Error rate | `error_rate` | 0.01 | P2 | yes |
| Node CPU saturation | `cpu_utilization` | 0.8 | P3 | no |
| Node NotReady | `node_ready` | 0 | P1 | yes |

## 告警组

| # | 组 | 级别 | 成员 | 抑制 | 根因 | Brain | 误判风险 |
|---:|---|---|---:|---:|---|---|---:|
| 1 | node-2 节点故障组 | P1 | 32 | 27 | `node-2` | laya / page | 4% |
| 2 | checkout-service 告警组 | P2 | 18 | 0 | `checkout-service` | laya / page | 8% |

## 静默

- `node-3` Node CPU saturation (P3) at 2026-11-01T16:10:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:11:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:12:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:13:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:14:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:15:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:16:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:17:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:18:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:19:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:20:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:21:00+00:00
- `node-3` Node CPU saturation (P3) at 2026-11-01T16:25:00+00:00
