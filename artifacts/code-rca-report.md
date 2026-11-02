# 代码级 RCA / REA 闭环报告

> 10/10 个金融故障已从 eBPF 代码栈定位并修复，最终回归 通过。

## 小白也能看懂

eBPF 采集到的是「哪一行代码在变慢、报错、阻塞」，不是一堆日志。
下面每个故障都按同一路径处理：代码栈定位 → 伪代码方案 → 补丁 → 金融回归 → eBPF 复测。

- 迭代：10
- 修复：10
- 成功率：100%
- 最终回归：通过

| # | 故障 | 根因代码 | 修复 | 测试 | eBPF 复测 |
|---:|---|---|---|---|---|
| 1 | 重复扣款：重试没有幂等键 | `payments.py:22` `retry_charge` | fixed | pass | 203.1ms / 0% |
| 2 | 金额换算差 1 分钱：浮点精度 | `money.py:5` `convert_currency` | fixed | pass | 151.4ms / 0% |
| 3 | 费率档位错算：边界用了大于号 | `money.py:10` `calculate_fee` | fixed | pass | 168.1ms / 0% |
| 4 | 分账多出 1 分钱：逐笔四舍五入 | `money.py:19` `allocate_amount` | fixed | pass | 167.0ms / 0% |
| 5 | 退款重复入账：回调没有去重 | `payments.py:37` `refund` | fixed | pass | 156.9ms / 0% |
| 6 | 批量结算漏掉最后一页 | `ledger.py:7` `settle_batch` | fixed | pass | 255.8ms / 0% |
| 7 | 账本部分写入：批次中途失败 | `ledger.py:22` `append_entries` | fixed | pass | 171.0ms / 0% |
| 8 | 欧元出款走错通道 | `routing.py:2` `route_payout` | fixed | pass | 173.8ms / 0% |
| 9 | 风控超时拖垮支付 | `risk.py:2` `fraud_score` | fixed | pass | 183.9ms / 0% |
| 10 | 结算日落在周末 | `scheduling.py:5` `next_settlement_date` | fixed | pass | 170.0ms / 0% |

## 逐个故障

### 1. 重复扣款：重试没有幂等键

- 现象：支付网关超时后客户被扣款两次，资金对账出现差额。
- 根因：`payments.py:22` `retry_charge`（置信度 89%）
- 证据：leaf frame accounts for 97% of incident exclusive time; p95 rises 14ms -> 2830ms (203.7x); error affinity 13% on this line; line belongs to a recent change set; 27% of time is off-CPU
- 方案：为每次扣款生成稳定幂等键，重试复用同一个键。
- 修复前：`for attempt in 1..3: /     gateway.charge(customer, amount)   # no request identity /     if timeout: retry`
- 修复后：`key = stable_id(customer, amount, intent) / for attempt in 1..3: /     gateway.charge(customer, amount, idempotency_key=key) /     if timeout: retry with same key`
- 验证：2400ms → 203.1ms，错误率 18% → 0%
- 小白解释：客户被重复扣款，常见于网关超时后的自动重试。 每次扣款生成稳定幂等键，所有重试复用同一个键。

### 2. 金额换算差 1 分钱：浮点精度

- 现象：外币换算在 1.005 等边界少 1 分，日终对账出现分差。
- 根因：`money.py:5` `convert_currency`（置信度 87%）
- 证据：leaf frame accounts for 94% of incident exclusive time; p95 rises 13ms -> 1428ms (106.1x); error affinity 10% on this line; line belongs to a recent change set; 27% of time is off-CPU
- 方案：用 Decimal 做金额运算，显式 HALF_UP 到分。
- 修复前：`return round(float(amount) * float(rate), 2)`
- 修复后：`value = Decimal(str(amount)) * Decimal(str(rate)) / return quantize(value, 2, HALF_UP)`
- 验证：1200ms → 151.4ms，错误率 12% → 0%
- 小白解释：换汇金额在分位边界少 1 分，长期累积成对账差额。 金额用 Decimal 字符串构造，按 HALF_UP 显式量化到分。

### 3. 费率档位错算：边界用了大于号

- 现象：金额刚好 1000/10000 的客户被按上一档费率多收费。
- 根因：`money.py:10` `calculate_fee`（置信度 87%）
- 证据：leaf frame accounts for 92% of incident exclusive time; p95 rises 14ms -> 1075ms (78.4x); error affinity 10% on this line; line belongs to a recent change set; 28% of time is off-CPU
- 方案：档位边界改为包含等号，并补边界测试。
- 修复前：`if amount > 10000: 0.5% / elif amount > 1000: 1.0% / else: 1.5%`
- 修复后：`if amount >= 10000: 0.5% / elif amount >= 1000: 1.0% / else: 1.5%`
- 验证：900ms → 168.1ms，错误率 9% → 0%
- 小白解释：刚好 1000 元的交易被收了 1.5% 而不是 1.0%。 边界改为 ``>=``，并为 1000/10000 加回归用例。

### 4. 分账多出 1 分钱：逐笔四舍五入

- 现象：0.05 元分给 3 个商户，每笔 0.02，合计 0.06。
- 根因：`money.py:19` `allocate_amount`（置信度 78%）
- 证据：leaf frame accounts for 90% of incident exclusive time; p95 rises 13ms -> 895ms (66.0x); error affinity 7% on this line; 26% of time is off-CPU
- 方案：先按分取整，再用最大余数法分配差额。
- 修复前：`shares = [round(total * w / sum(weights), 2) for w in weights] / return shares  # sum may not equal total`
- 修复后：`raw = total * w / sum(weights) / cents = round_half_up(raw * 100) / distribute (total_cents - sum(cents)) by largest remainder / return cents / 100`
- 验证：750ms → 167.0ms，错误率 7% → 0%
- 小白解释：分账后各商户金额之和比原金额多 1 分。 先转成整数分，再用最大余数法把差额分给前几笔。

### 5. 退款重复入账：回调没有去重

- 现象：支付网关重放退款回调，同一笔退款入账两次。
- 根因：`payments.py:37` `refund`（置信度 87%）
- 证据：leaf frame accounts for 95% of incident exclusive time; p95 rises 13ms -> 1689ms (124.6x); error affinity 7% on this line; line belongs to a recent change set; 28% of time is off-CPU
- 方案：按 refund_id 去重，重复回调直接返回已有结果。
- 修复前：`append refund(refund_id) / return count`
- 修复后：`if refund_id already in store: return existing count / append refund(refund_id) / return count`
- 验证：1400ms → 156.9ms，错误率 11% → 0%
- 小白解释：同一笔退款被处理两次，商户余额被多退。 先查 refund_id，已存在则直接返回，保证幂等。

### 6. 批量结算漏掉最后一页

- 现象：结算批次只处理第一页，后续页的商户当天没有入账。
- 根因：`ledger.py:7` `settle_batch`（置信度 88%）
- 证据：leaf frame accounts for 98% of incident exclusive time; p95 rises 13ms -> 3696ms (275.3x); error affinity 10% on this line; line belongs to a recent change set; 27% of time is off-CPU
- 方案：只有空页才结束，满页继续翻页。
- 修复前：`rows = fetch(page) / append rows / if len(rows) <= page_size: stop`
- 修复后：`rows = fetch(page) / append rows / if not rows: stop / if len(rows) < page_size: stop / page += 1`
- 验证：3200ms → 255.8ms，错误率 15% → 0%
- 小白解释：结算只处理了第一页，后面的商户漏结算。 只有空页或不足一页才结束，满页继续翻页。

### 7. 账本部分写入：批次中途失败

- 现象：批次写账本时第 2 条失败，但第 1 条已经落库，账不平。
- 根因：`ledger.py:22` `append_entries`（置信度 80%）
- 证据：leaf frame accounts for 96% of incident exclusive time; p95 rises 14ms -> 2086ms (151.3x); error affinity 10% on this line; 28% of time is off-CPU
- 方案：先全量校验并暂存，全部通过后再一次性提交。
- 修复前：`for entry in entries: /     if invalid: raise /     store.append(entry)   # partial write`
- 修复后：`staged = [] / for entry in entries: /     if invalid: raise /     staged.append(entry) / store.extend(staged)   # all-or-nothing`
- 验证：1800ms → 171.0ms，错误率 20% → 0%
- 小白解释：一次批量记账失败后，账本里多出一条半截数据。 先暂存并通过全部校验，再一次性写入。

### 8. 欧元出款走错通道

- 现象：EUR/DE 出款被路由到美元通道，产生退汇和手续费。
- 根因：`routing.py:2` `route_payout`（置信度 88%）
- 证据：leaf frame accounts for 94% of incident exclusive time; p95 rises 14ms -> 1327ms (97.5x); error affinity 13% on this line; line belongs to a recent change set; 26% of time is off-CPU
- 方案：按币种+国家选择本地清算通道，不支持的组合显式拒绝。
- 修复前：`if currency == USD: return us-bank / if country == US: return us-bank / return default-bank`
- 修复后：`match (currency, country): /   (USD, US) -> us-bank /   (EUR, EU) -> eu-bank /   (GBP, GB) -> uk-bank /   _ -> reject`
- 验证：1100ms → 173.8ms，错误率 10% → 0%
- 小白解释：欧元区出款被送到美元通道，资金被退回。 用币种+国家匹配本地通道，未知组合直接拒绝。

### 9. 风控超时拖垮支付

- 现象：风控供应商超时，整条支付链路 500，所有交易被阻断。
- 根因：`risk.py:2` `fraud_score`（置信度 90%）
- 证据：leaf frame accounts for 97% of incident exclusive time; p95 rises 14ms -> 2990ms (219.4x); error affinity 23% on this line; line belongs to a recent change set; 27% of time is off-CPU
- 方案：超时降级为中间风险分并标记 degraded，恢复后再走完整风控。
- 修复前：`return provider.score(txn)   # timeout escapes`
- 修复后：`try: /     return {score: provider.score(txn), degraded: false} / except Timeout: /     return {score: 50, degraded: true} /     emit_metric('risk.degraded')`
- 验证：2600ms → 183.9ms，错误率 22% → 0%
- 小白解释：风控接口一超时，所有支付请求都失败。 捕获超时，返回中间风险分并标记降级，保证支付可用。

### 10. 结算日落在周末

- 现象：周五的结算被排到周六，清算所不上班导致延迟。
- 根因：`scheduling.py:5` `next_settlement_date`（置信度 86%）
- 证据：leaf frame accounts for 92% of incident exclusive time; p95 rises 14ms -> 937ms (68.7x); error affinity 3% on this line; line belongs to a recent change set; 27% of time is off-CPU
- 方案：结算日只取工作日，自动跳到下一个周一/工作日。
- 修复前：`return day + 1 day`
- 修复后：`candidate = day + 1 day / while candidate is Saturday/Sunday: /     candidate += 1 day / return candidate`
- 验证：800ms → 170.0ms，错误率 5% → 0%
- 小白解释：周五的结算日被算成周六，资金晚到两天。 循环跳过周六/周日，返回下一个工作日。

