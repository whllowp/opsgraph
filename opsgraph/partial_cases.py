"""Partial-observability case for the 60% eBPF coverage incident.

The inference layer never reads ``expected_root``. The fixture exists so the
demo can show a blind internal service (ledger) being diagnosed from metrics,
logs, config and knowledge rather than pretending eBPF observed everything.
"""

from __future__ import annotations

SERVICES = [
    {"name": "frontend", "covered": True, "role": "upstream"},
    {"name": "checkout-service", "covered": True, "role": "caller"},
    {"name": "payment-gateway", "covered": True, "role": "victim"},
    {"name": "payment-router", "covered": True, "role": "downstream"},
    {"name": "ledger-service", "covered": False, "role": "blind-root-candidate"},
    {"name": "risk-control", "covered": False, "role": "blind"},
    {"name": "cart-service", "covered": True, "role": "downstream"},
    {"name": "order-service", "covered": True, "role": "downstream"},
    {"name": "inventory-service", "covered": False, "role": "blind"},
    {"name": "notification-service", "covered": False, "role": "blind"},
]

EDGES = [
    {"src": "frontend", "dst": "checkout-service", "covered": True, "p99_ms": 210},
    {"src": "frontend", "dst": "cart-service", "covered": True, "p99_ms": 90},
    {"src": "frontend", "dst": "order-service", "covered": True, "p99_ms": 180},
    {"src": "checkout-service", "dst": "payment-gateway", "covered": True, "p99_ms": 1620},
    {"src": "payment-gateway", "dst": "ledger-service", "covered": False, "p99_ms": 0},
    {"src": "payment-gateway", "dst": "risk-control", "covered": True, "p99_ms": 310},
    {"src": "payment-gateway", "dst": "payment-router", "covered": True, "p99_ms": 1500},
    {"src": "payment-router", "dst": "ledger-service", "covered": False, "p99_ms": 0},
    {"src": "cart-service", "dst": "inventory-service", "covered": True, "p99_ms": 140},
    {"src": "order-service", "dst": "inventory-service", "covered": False, "p99_ms": 0},
    {"src": "order-service", "dst": "ledger-service", "covered": False, "p99_ms": 0},
    {"src": "checkout-service", "dst": "notification-service", "covered": True, "p99_ms": 120},
    {"src": "payment-router", "dst": "risk-control", "covered": True, "p99_ms": 280},
    {"src": "ledger-service", "dst": "notification-service", "covered": False, "p99_ms": 0},
    {"src": "cart-service", "dst": "checkout-service", "covered": False, "p99_ms": 0},
]

INCIDENT = {
    "case_id": "partial-60-ledger-pool",
    "title": "支付 P99 故障，eBPF 只覆盖 60%",
    "victim": "payment-gateway",
    "slo_p99_ms": 800,
    "window": "2026-11-02 00:00-01:00",
    "observed": {
        "payment-gateway": {
            "source": "ebpf",
            "p99_ms": 1620,
            "baseline_p99_ms": 110,
            "error_rate": 0.018,
            "qps": 3100,
        },
        "ledger-service": {
            "source": "apm",
            "p99_ms": 1480,
            "pool_active": 10,
            "pool_max": 10,
            "pool_wait_ms": 420,
            "error_rate": 0.02,
        },
        "payment-router": {"source": "ebpf", "p99_ms": 1500, "baseline_p99_ms": 90},
    },
    "root_hint": "ledger-service",
    "root_cause": "数据库连接池耗尽（max_pool 50 -> 10 后泄漏）",
    "known_log": "ledger-service: connection pool exhausted; timeout waiting for connection",
    "config_change": "ledger-service DB max_pool 50 -> 10 at 00:12",
}

# Probes are ordered by a deterministic cost-aware diagnosis plan. The inference
# engine reads likelihoods and observations, never ``root_hint``.
PROBES = [
    {
        "id": "coverage-audit",
        "title": "审计 eBPF 覆盖率与盲区",
        "source": "coverage",
        "cost": 0.2,
        "observations": [
            "10 个服务中只有 6 个有 eBPF 内部探针",
            "ledger/risk/inventory/notification 没有进程内栈",
            "5 条支付路径边没有 eBPF 流量，不能把“没观测到”当成健康",
        ],
        "likelihoods": {
            "ledger_pool": 0.6,
            "gateway_overload": 0.6,
            "checkout_retry": 0.6,
            "network_latency": 0.6,
            "node_pressure": 0.6,
        },
    },
    {
        "id": "ebpf-path",
        "title": "从已覆盖的 eBPF 边看支付路径",
        "source": "ebpf",
        "cost": 0.3,
        "observations": [
            "checkout -> payment-gateway P99 1620ms，出向放大",
            "payment-gateway -> payment-router P99 1500ms",
            "payment-gateway -> ledger 边没有 eBPF 数据，是盲区而非正常",
        ],
        "likelihoods": {
            "ledger_pool": 0.68,
            "gateway_overload": 0.42,
            "checkout_retry": 0.5,
            "network_latency": 0.82,
            "node_pressure": 0.35,
        },
    },
    {
        "id": "apm-metrics",
        "title": "查询 APM 指标补足盲区",
        "source": "metrics",
        "cost": 0.5,
        "observations": [
            "ledger-service P99 1480ms，错误率 2%",
            "DB pool active=10/max=10，等待 420ms",
            "payment-gateway 自身 CPU/队列正常，像是下游阻塞",
        ],
        "likelihoods": {
            "ledger_pool": 0.94,
            "gateway_overload": 0.3,
            "checkout_retry": 0.3,
            "network_latency": 0.45,
            "node_pressure": 0.2,
        },
    },
    {
        "id": "log-signature",
        "title": "检索盲区服务的错误日志",
        "source": "logs",
        "cost": 0.6,
        "observations": [
            "ledger-service: connection pool exhausted; timeout waiting for connection",
            "错误集中在 00:13 之后，与 pool 配置变更时间一致",
        ],
        "likelihoods": {
            "ledger_pool": 0.97,
            "gateway_overload": 0.2,
            "checkout_retry": 0.2,
            "network_latency": 0.3,
            "node_pressure": 0.15,
        },
    },
    {
        "id": "config-diff",
        "title": "对比变更配置",
        "source": "config",
        "cost": 0.4,
        "observations": [
            "ledger-service DB max_pool 50 -> 10 at 00:12",
            "变更与 pool exhausted 日志只差 1 分钟",
        ],
        "likelihoods": {
            "ledger_pool": 0.985,
            "gateway_overload": 0.1,
            "checkout_retry": 0.15,
            "network_latency": 0.2,
            "node_pressure": 0.08,
        },
    },
    {
        "id": "knowledge-match",
        "title": "匹配历史故障知识",
        "source": "knowledge",
        "cost": 0.2,
        "observations": [
            "命中历史签名 db-pool-exhaustion",
            "pool_active==max + wait>200ms + max_pool 下调 = 池耗尽",
        ],
        "likelihoods": {
            "ledger_pool": 0.99,
            "gateway_overload": 0.06,
            "checkout_retry": 0.08,
            "network_latency": 0.12,
            "node_pressure": 0.05,
        },
    },
    {
        "id": "pool-probe",
        "title": "主动执行连接池探针",
        "source": "synthetic",
        "cost": 0.8,
        "observations": [
            "获取连接超时；活跃连接无法释放",
            "重启连接池后探针恢复，证明不是网络问题",
        ],
        "likelihoods": {
            "ledger_pool": 0.998,
            "gateway_overload": 0.04,
            "checkout_retry": 0.04,
            "network_latency": 0.06,
            "node_pressure": 0.03,
        },
    },
]

RECOVERY = {
    "action": "回滚 ledger-service DB max_pool 10 -> 50 并滚动重启连接池",
    "why": "配置变更与 pool 等待同时发生；回滚是最小、可逆、可验证的恢复动作。",
    "before": {"payment_gateway_p99_ms": 1620, "error_rate": 0.018, "pool_wait_ms": 420},
    "after": {"payment_gateway_p99_ms": 620, "error_rate": 0.004, "pool_wait_ms": 30},
    "coverage_fix": "为 ledger-service 增加 DB 连接池 eBPF uprobe；覆盖率 60% -> 70%",
}
