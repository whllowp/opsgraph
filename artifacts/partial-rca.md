# 60% 覆盖下的 RCA / 恢复报告

> 支付 P99 故障，eBPF 只覆盖 60%

- 根因：`ledger-service` / DB 连接池耗尽
- 置信度：99.0%
- eBPF 覆盖：60.0% 服务 / 60.0% 边
- 盲区：ledger-service, risk-control, inventory-service, notification-service
- 恢复：PASS · P99 620ms

## 十次迭代

| # | 阶段 | 动作 | 来源 | 最高假设 | 置信度 |
|---:|---|---|---|---|---:|
| 1 | probe | 审计 eBPF 覆盖率与盲区 | coverage | gateway_overload | 25% |
| 2 | probe | 从已覆盖的 eBPF 边看支付路径 | ebpf | network_latency | 29% |
| 3 | probe | 查询 APM 指标补足盲区 | metrics | ledger_pool | 47% |
| 4 | probe | 检索盲区服务的错误日志 | logs | ledger_pool | 77% |
| 5 | probe | 对比变更配置 | config | ledger_pool | 95% |
| 6 | probe | 匹配历史故障知识 | knowledge | ledger_pool | 99% |
| 7 | probe | 主动执行连接池探针 | synthetic | ledger_pool | 99% |
| 8 | remediation | 回滚 ledger-service DB max_pool 10 -> 50 并滚动重启连接池 | config | ledger_pool | 99% |
| 9 | verify | 用 SLO 与错误率验证恢复 | slo | ledger_pool | 99% |
| 10 | coverage | 为 ledger-service 增加 DB 连接池 eBPF uprobe；覆盖率 60% -> 70% | coverage | - | 0% |

## 恢复

- 回滚 ledger-service DB max_pool 10 -> 50 并滚动重启连接池

## 证据缺口

盲区服务没有进程内 eBPF，因此不能把缺失的 span 当成健康；本次通过 APM、日志、配置 diff 和知识签名完成推断。
