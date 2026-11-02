"""Ten financial incidents used by the eBPF code-RCA / REA demo.

The RCA engine never reads ``patch`` or ``novice``; those fields are the
remediation plan and the human explanation. The stack samples live in
``data/code_rca/ebpf_samples.json`` and are generated from the same cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FINTECH_ROOT = PROJECT_ROOT / "demo" / "fintech"
CODE_RCA_DIR = PROJECT_ROOT / "data" / "code_rca"

CASES: list[dict[str, Any]] = [
    {
        "id": "case-01",
        "title": "重复扣款：重试没有幂等键",
        "service": "payment-orchestrator",
        "severity": "critical",
        "file": "payments.py",
        "function": "retry_charge",
        "test": "test_retry_charge_is_idempotent",
        "slo_p99_ms": 800,
        "before_p99_ms": 2400,
        "after_p99_ms": 180,
        "before_error_rate": 0.18,
        "after_error_rate": 0.0,
        "symptom": "支付网关超时后客户被扣款两次，资金对账出现差额。",
        "strategy": "为每次扣款生成稳定幂等键，重试复用同一个键。",
        "patch": {
            "old": """def retry_charge(gateway, customer_id, amount, attempts=3):
    # BUG: retries are not idempotent, so a timeout can charge the customer twice.
    for _ in range(attempts):
        try:
            return gateway.charge(customer_id, amount)
        except TimeoutError:
            continue
    raise RuntimeError("gateway unavailable")""",
            "new": """def retry_charge(gateway, customer_id, amount, attempts=3):
    key = f"charge:{customer_id}:{amount}"
    for _ in range(attempts):
        try:
            return gateway.charge(customer_id, amount, idempotency_key=key)
        except TimeoutError:
            continue
    raise RuntimeError("gateway unavailable")""",
        },
        "pseudocode_before": [
            "for attempt in 1..3:",
            "    gateway.charge(customer, amount)   # no request identity",
            "    if timeout: retry",
        ],
        "pseudocode_after": [
            "key = stable_id(customer, amount, intent)",
            "for attempt in 1..3:",
            "    gateway.charge(customer, amount, idempotency_key=key)",
            "    if timeout: retry with same key",
        ],
        "novice": {
            "what": "客户被重复扣款，常见于网关超时后的自动重试。",
            "why": "重试请求没有幂等键，网关无法判断这是同一次扣款。",
            "fix": "每次扣款生成稳定幂等键，所有重试复用同一个键。",
            "verify": "重复回调后只保留 1 笔 charge，资金不再多扣。",
        },
    },
    {
        "id": "case-02",
        "title": "金额换算差 1 分钱：浮点精度",
        "service": "fx-pricing",
        "severity": "high",
        "file": "money.py",
        "function": "convert_currency",
        "test": "test_convert_currency_rounds_half_up",
        "slo_p99_ms": 500,
        "before_p99_ms": 1200,
        "after_p99_ms": 95,
        "before_error_rate": 0.12,
        "after_error_rate": 0.0,
        "symptom": "外币换算在 1.005 等边界少 1 分，日终对账出现分差。",
        "strategy": "用 Decimal 做金额运算，显式 HALF_UP 到分。",
        "patch": {
            "old": """def convert_currency(amount, rate):
    # BUG: float arithmetic loses cents before the ledger records the amount.
    return round(float(amount) * float(rate), 2)""",
            "new": """def convert_currency(amount, rate):
    value = Decimal(str(amount)) * Decimal(str(rate))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))""",
        },
        "pseudocode_before": [
            "return round(float(amount) * float(rate), 2)",
        ],
        "pseudocode_after": [
            "value = Decimal(str(amount)) * Decimal(str(rate))",
            "return quantize(value, 2, HALF_UP)",
        ],
        "novice": {
            "what": "换汇金额在分位边界少 1 分，长期累积成对账差额。",
            "why": "二进制浮点无法精确表示 1.005，round 会向下取整。",
            "fix": "金额用 Decimal 字符串构造，按 HALF_UP 显式量化到分。",
            "verify": "1.005 换算为 1.01，边界金额与账本一致。",
        },
    },
    {
        "id": "case-03",
        "title": "费率档位错算：边界用了大于号",
        "service": "billing",
        "severity": "high",
        "file": "money.py",
        "function": "calculate_fee",
        "test": "test_fee_boundary_is_inclusive",
        "slo_p99_ms": 400,
        "before_p99_ms": 900,
        "after_p99_ms": 80,
        "before_error_rate": 0.09,
        "after_error_rate": 0.0,
        "symptom": "金额刚好 1000/10000 的客户被按上一档费率多收费。",
        "strategy": "档位边界改为包含等号，并补边界测试。",
        "patch": {
            "old": """def calculate_fee(amount, tier):
    # BUG: tier boundaries are exclusive, so exactly 1000/10000 is overcharged.
    if amount > 10000:
        return round(amount * 0.005, 2)
    if amount > 1000:
        return round(amount * 0.01, 2)
    return round(amount * 0.015, 2)""",
            "new": """def calculate_fee(amount, tier):
    if amount >= 10000:
        return round(amount * 0.005, 2)
    if amount >= 1000:
        return round(amount * 0.01, 2)
    return round(amount * 0.015, 2)""",
        },
        "pseudocode_before": [
            "if amount > 10000: 0.5%",
            "elif amount > 1000: 1.0%",
            "else: 1.5%",
        ],
        "pseudocode_after": [
            "if amount >= 10000: 0.5%",
            "elif amount >= 1000: 1.0%",
            "else: 1.5%",
        ],
        "novice": {
            "what": "刚好 1000 元的交易被收了 1.5% 而不是 1.0%。",
            "why": "档位判断使用 ``>``，把边界金额归到了上一档。",
            "fix": "边界改为 ``>=``，并为 1000/10000 加回归用例。",
            "verify": "1000 元收 10 元，10000 元收 50 元。",
        },
    },
    {
        "id": "case-04",
        "title": "分账多出 1 分钱：逐笔四舍五入",
        "service": "settlement",
        "severity": "high",
        "file": "money.py",
        "function": "allocate_amount",
        "test": "test_allocate_amount_preserves_total",
        "slo_p99_ms": 600,
        "before_p99_ms": 750,
        "after_p99_ms": 60,
        "before_error_rate": 0.07,
        "after_error_rate": 0.0,
        "symptom": "0.05 元分给 3 个商户，每笔 0.02，合计 0.06。",
        "strategy": "先按分取整，再用最大余数法分配差额。",
        "patch": {
            "old": """def allocate_amount(total, weights):
    # BUG: independent rounding loses or creates cents when splitting money.
    total_weight = sum(weights)
    return [round(total * weight / total_weight, 2) for weight in weights]""",
            "new": """def allocate_amount(total, weights):
    total_weight = sum(weights)
    raw = [
        Decimal(str(total)) * Decimal(weight) / Decimal(total_weight)
        for weight in weights
    ]
    cents = [
        int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        for value in raw
    ]
    diff = int((Decimal(str(total)) * 100).quantize(Decimal("1"))) - sum(cents)
    for index in range(abs(diff)):
        cents[index % len(cents)] += 1 if diff > 0 else -1
    return [float(Decimal(c) / 100) for c in cents]""",
        },
        "pseudocode_before": [
            "shares = [round(total * w / sum(weights), 2) for w in weights]",
            "return shares  # sum may not equal total",
        ],
        "pseudocode_after": [
            "raw = total * w / sum(weights)",
            "cents = round_half_up(raw * 100)",
            "distribute (total_cents - sum(cents)) by largest remainder",
            "return cents / 100",
        ],
        "novice": {
            "what": "分账后各商户金额之和比原金额多 1 分。",
            "why": "每笔独立四舍五入，误差不会自动回冲。",
            "fix": "先转成整数分，再用最大余数法把差额分给前几笔。",
            "verify": "0.05 元分 3 份得到 0.01/0.02/0.02，合计仍是 0.05。",
        },
    },
    {
        "id": "case-05",
        "title": "退款重复入账：回调没有去重",
        "service": "refund-service",
        "severity": "critical",
        "file": "payments.py",
        "function": "refund",
        "test": "test_refund_is_idempotent",
        "slo_p99_ms": 700,
        "before_p99_ms": 1400,
        "after_p99_ms": 120,
        "before_error_rate": 0.11,
        "after_error_rate": 0.0,
        "symptom": "支付网关重放退款回调，同一笔退款入账两次。",
        "strategy": "按 refund_id 去重，重复回调直接返回已有结果。",
        "patch": {
            "old": """def refund(store, charge_id, amount, refund_id):
    # BUG: duplicate refund callbacks append a second refund for the same id.
    store.refunds.append({"charge_id": charge_id, "amount": amount, "refund_id": refund_id})
    return len(store.refunds)""",
            "new": """def refund(store, charge_id, amount, refund_id):
    for item in store.refunds:
        if item["refund_id"] == refund_id:
            return len(store.refunds)
    store.refunds.append({"charge_id": charge_id, "amount": amount, "refund_id": refund_id})
    return len(store.refunds)""",
        },
        "pseudocode_before": [
            "append refund(refund_id)",
            "return count",
        ],
        "pseudocode_after": [
            "if refund_id already in store: return existing count",
            "append refund(refund_id)",
            "return count",
        ],
        "novice": {
            "what": "同一笔退款被处理两次，商户余额被多退。",
            "why": "回调处理只追加记录，没有按 refund_id 去重。",
            "fix": "先查 refund_id，已存在则直接返回，保证幂等。",
            "verify": "同一 refund_id 调用两次，只产生 1 条退款记录。",
        },
    },
    {
        "id": "case-06",
        "title": "批量结算漏掉最后一页",
        "service": "settlement",
        "severity": "high",
        "file": "ledger.py",
        "function": "settle_batch",
        "test": "test_settle_batch_reads_all_pages",
        "slo_p99_ms": 1000,
        "before_p99_ms": 3200,
        "after_p99_ms": 220,
        "before_error_rate": 0.15,
        "after_error_rate": 0.0,
        "symptom": "结算批次只处理第一页，后续页的商户当天没有入账。",
        "strategy": "只有空页才结束，满页继续翻页。",
        "patch": {
            "old": """def settle_batch(fetch_page, page_size=2):
    # BUG: a full page is treated as the last page, so later pages are skipped.
    page = 0
    settled = []
    while True:
        rows = fetch_page(page, page_size)
        if not rows:
            break
        settled.extend(rows)
        if len(rows) <= page_size:
            break
        page += 1
    return settled""",
            "new": """def settle_batch(fetch_page, page_size=2):
    page = 0
    settled = []
    while True:
        rows = fetch_page(page, page_size)
        if not rows:
            break
        settled.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    return settled""",
        },
        "pseudocode_before": [
            "rows = fetch(page)",
            "append rows",
            "if len(rows) <= page_size: stop",
        ],
        "pseudocode_after": [
            "rows = fetch(page)",
            "append rows",
            "if not rows: stop",
            "if len(rows) < page_size: stop",
            "page += 1",
        ],
        "novice": {
            "what": "结算只处理了第一页，后面的商户漏结算。",
            "why": "代码把「满页」当成「最后一页」，提前退出循环。",
            "fix": "只有空页或不足一页才结束，满页继续翻页。",
            "verify": "3 页数据全部进入结算列表，不再漏单。",
        },
    },
    {
        "id": "case-07",
        "title": "账本部分写入：批次中途失败",
        "service": "ledger",
        "severity": "critical",
        "file": "ledger.py",
        "function": "append_entries",
        "test": "test_append_entries_is_atomic",
        "slo_p99_ms": 500,
        "before_p99_ms": 1800,
        "after_p99_ms": 140,
        "before_error_rate": 0.2,
        "after_error_rate": 0.0,
        "symptom": "批次写账本时第 2 条失败，但第 1 条已经落库，账不平。",
        "strategy": "先全量校验并暂存，全部通过后再一次性提交。",
        "patch": {
            "old": """def append_entries(store, entries):
    # BUG: a mid-batch failure leaves the ledger partially written.
    for entry in entries:
        if entry.get("fail"):
            raise ValueError("ledger write failed")
        store.entries.append(entry)""",
            "new": """def append_entries(store, entries):
    staged = []
    for entry in entries:
        if entry.get("fail"):
            raise ValueError("ledger write failed")
        staged.append(entry)
    store.entries.extend(staged)""",
        },
        "pseudocode_before": [
            "for entry in entries:",
            "    if invalid: raise",
            "    store.append(entry)   # partial write",
        ],
        "pseudocode_after": [
            "staged = []",
            "for entry in entries:",
            "    if invalid: raise",
            "    staged.append(entry)",
            "store.extend(staged)   # all-or-nothing",
        ],
        "novice": {
            "what": "一次批量记账失败后，账本里多出一条半截数据。",
            "why": "边校验边写入，异常发生时前面的条目已经提交。",
            "fix": "先暂存并通过全部校验，再一次性写入。",
            "verify": "失败批次不改变账本，重试后账目一致。",
        },
    },
    {
        "id": "case-08",
        "title": "欧元出款走错通道",
        "service": "payout-router",
        "severity": "high",
        "file": "routing.py",
        "function": "route_payout",
        "test": "test_route_payout_uses_local_rail",
        "slo_p99_ms": 600,
        "before_p99_ms": 1100,
        "after_p99_ms": 90,
        "before_error_rate": 0.1,
        "after_error_rate": 0.0,
        "symptom": "EUR/DE 出款被路由到美元通道，产生退汇和手续费。",
        "strategy": "按币种+国家选择本地清算通道，不支持的组合显式拒绝。",
        "patch": {
            "old": """def route_payout(currency, country):
    # BUG: non-USD payouts fall through to a default rail with the wrong currency.
    if currency == "USD":
        return "us-bank"
    if country == "US":
        return "us-bank"
    return "default-bank\"""",
            "new": """def route_payout(currency, country):
    if currency == "USD" and country == "US":
        return "us-bank"
    if currency == "EUR" and country in {"DE", "FR", "NL", "ES", "IT"}:
        return "eu-bank"
    if currency == "GBP" and country == "GB":
        return "uk-bank"
    raise ValueError("unsupported payout rail")""",
        },
        "pseudocode_before": [
            "if currency == USD: return us-bank",
            "if country == US: return us-bank",
            "return default-bank",
        ],
        "pseudocode_after": [
            "match (currency, country):",
            "  (USD, US) -> us-bank",
            "  (EUR, EU) -> eu-bank",
            "  (GBP, GB) -> uk-bank",
            "  _ -> reject",
        ],
        "novice": {
            "what": "欧元区出款被送到美元通道，资金被退回。",
            "why": "路由只判断了 USD 或 US，其他组合落入 default。",
            "fix": "用币种+国家匹配本地通道，未知组合直接拒绝。",
            "verify": "EUR/DE 走 eu-bank，GBP/GB 走 uk-bank。",
        },
    },
    {
        "id": "case-09",
        "title": "风控超时拖垮支付",
        "service": "risk-gateway",
        "severity": "critical",
        "file": "risk.py",
        "function": "fraud_score",
        "test": "test_fraud_score_degrades_on_timeout",
        "slo_p99_ms": 700,
        "before_p99_ms": 2600,
        "after_p99_ms": 160,
        "before_error_rate": 0.22,
        "after_error_rate": 0.0,
        "symptom": "风控供应商超时，整条支付链路 500，所有交易被阻断。",
        "strategy": "超时降级为中间风险分并标记 degraded，恢复后再走完整风控。",
        "patch": {
            "old": """def fraud_score(provider, txn):
    # BUG: provider timeout propagates and blocks every payment in the request.
    return provider.score(txn)""",
            "new": """def fraud_score(provider, txn):
    try:
        return {"score": provider.score(txn), "degraded": False}
    except TimeoutError:
        return {"score": 50, "degraded": True}""",
        },
        "pseudocode_before": [
            "return provider.score(txn)   # timeout escapes",
        ],
        "pseudocode_after": [
            "try:",
            "    return {score: provider.score(txn), degraded: false}",
            "except Timeout:",
            "    return {score: 50, degraded: true}",
            "    emit_metric('risk.degraded')",
        ],
        "novice": {
            "what": "风控接口一超时，所有支付请求都失败。",
            "why": "fraud_score 没有超时兜底，异常直接冒泡到支付主流程。",
            "fix": "捕获超时，返回中间风险分并标记降级，保证支付可用。",
            "verify": "供应商超时返回 score=50/degraded=true，支付继续。",
        },
    },
    {
        "id": "case-10",
        "title": "结算日落在周末",
        "service": "settlement-calendar",
        "severity": "medium",
        "file": "scheduling.py",
        "function": "next_settlement_date",
        "test": "test_next_settlement_date_skips_weekend",
        "slo_p99_ms": 300,
        "before_p99_ms": 800,
        "after_p99_ms": 70,
        "before_error_rate": 0.05,
        "after_error_rate": 0.0,
        "symptom": "周五的结算被排到周六，清算所不上班导致延迟。",
        "strategy": "结算日只取工作日，自动跳到下一个周一/工作日。",
        "patch": {
            "old": """def next_settlement_date(day):
    # BUG: weekends are treated as settlement days.
    return day + timedelta(days=1)""",
            "new": """def next_settlement_date(day):
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate""",
        },
        "pseudocode_before": [
            "return day + 1 day",
        ],
        "pseudocode_after": [
            "candidate = day + 1 day",
            "while candidate is Saturday/Sunday:",
            "    candidate += 1 day",
            "return candidate",
        ],
        "novice": {
            "what": "周五的结算日被算成周六，资金晚到两天。",
            "why": "日期计算只加 1 天，没有跳过周末。",
            "fix": "循环跳过周六/周日，返回下一个工作日。",
            "verify": "2026-11-06 周五结算日变为 2026-11-09 周一。",
        },
    },
]

CASE_BY_ID = {case["id"]: case for case in CASES}


def load_cases() -> list[dict[str, Any]]:
    return [dict(case) for case in CASES]
