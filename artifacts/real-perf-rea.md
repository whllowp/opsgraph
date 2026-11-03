# 真实 perf RCA / REA 报告

> 10/10 C 金融故障通过真实 perf 栈定位并修复。

- perf：`perf`
- 用例：10
- 修复：10
- 最终 C 回归：PASS

| # | 故障 | 根因 | 栈样本 | C 回归 | 修复后样本 |
|---:|---|---|---:|---|---:|
| 1 | 换汇截断分位 | `fintech.c:8` `convert_currency` | 61 | pass | 69 |
| 2 | 费率边界错算 | `fintech.c:18` `calculate_fee` | 84 | pass | 82 |
| 3 | 分账丢失余数 | `fintech.c:24` `allocate_amount` | 76 | pass | 80 |
| 4 | 重试重复扣款 | `fintech.c:32` `retry_charge` | 77 | pass | 77 |
| 5 | 退款重复入账 | `fintech.c:41` `refund` | 75 | pass | 79 |
| 6 | 批量结算漏页 | `fintech.c:49` `settle_batch` | 79 | pass | 79 |
| 7 | 账本部分写入 | `fintech.c:58` `append_entries` | 74 | pass | 78 |
| 8 | 欧元出款走错通道 | `fintech.c:70` `route_payout` | 81 | pass | 77 |
| 9 | 风控超时阻断支付 | `fintech.c:86` `fraud_score` | 63 | pass | 69 |
| 10 | 结算日落在周末 | `fintech.c:93` `next_settlement_date` | 58 | pass | 66 |
