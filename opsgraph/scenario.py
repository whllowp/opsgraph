"""Deterministic eBPF/DeepFlow replay for an operational incident.

The scenario models a checkout retry-storm:

* 00:10 a checkout config change raises retries from 2 to 5 and the timeout
  from 500ms to 2000ms;
* retries amplify checkout -> payment-gateway traffic;
* payment-gateway latency propagates to payment-router and the channels;
* 00:30 a flash sale adds 3.2x baseline traffic;
* a payment-gateway deployment from the previous evening and CPU pressure on
  node-3 are deliberate red herrings.

The output is a DeepFlow-shaped ``l7_flow_log`` aggregate (one row per minute,
per direction/endpoint) plus node metrics and the declared control-plane
topology. OpsGraph's ingest layer only reads the flow records and node metrics;
it never reads the expected answer below.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
from pathlib import Path
from typing import Any

from .config import (
    INCIDENT_ID,
    REPLAY_DIR,
    SCENARIO_START,
    VICTIM_SERVICE,
    WINDOW_MINUTES,
)
from .timeutil import clock, iso, ts
from .util import write_json

CLUSTER = {
    "name": "prod-shanghai",
    "display": "prod-shanghai",
    "region": "cn-shanghai",
    "az_count": 3,
    "kubernetes_version": "v1.31.4",
}

NODES = [
    {
        "name": "node-1",
        "az": "az-a",
        "instance_type": "c6.4xlarge",
        "cores": 16,
        "base_cpu": 0.48,
    },
    {
        "name": "node-2",
        "az": "az-a",
        "instance_type": "c6.4xlarge",
        "cores": 16,
        "base_cpu": 0.46,
    },
    {
        "name": "node-3",
        "az": "az-b",
        "instance_type": "c6.4xlarge",
        "cores": 16,
        "base_cpu": 0.35,
    },
    {
        "name": "node-4",
        "az": "az-b",
        "instance_type": "c6.2xlarge",
        "cores": 8,
        "base_cpu": 0.33,
    },
    {
        "name": "node-5",
        "az": "az-c",
        "instance_type": "c6.2xlarge",
        "cores": 8,
        "base_cpu": 0.30,
    },
    {
        "name": "node-6",
        "az": "az-c",
        "instance_type": "c6.2xlarge",
        "cores": 8,
        "base_cpu": 0.28,
    },
]

NAMESPACES = [
    {"name": "shop", "display": "shop", "team": "commerce-core"},
    {"name": "payments", "display": "payments", "team": "payments-backend"},
    {"name": "platform", "display": "platform", "team": "platform-sre"},
]

SERVICES = [
    {
        "name": "frontend",
        "display": "frontend-web",
        "namespace": "shop",
        "tier": "edge",
        "language": "typescript",
        "owner": "storefront",
        "criticality": "platinum",
        "version": "v4.8.2",
        "replicas": 3,
        "nodes": ["node-1", "node-2", "node-4"],
        "endpoints": [("GET", "/api/v1/home"), ("GET", "/api/v1/search")],
        "layout": (1, 0),
    },
    {
        "name": "checkout-service",
        "display": "checkout-service",
        "namespace": "shop",
        "tier": "core",
        "language": "go",
        "owner": "commerce-core",
        "criticality": "platinum",
        "version": "v2.14.0",
        "replicas": 3,
        "nodes": ["node-1", "node-2", "node-5"],
        "endpoints": [("POST", "/api/v1/checkout")],
        "layout": (2, 0),
    },
    {
        "name": "cart-service",
        "display": "cart-service",
        "namespace": "shop",
        "tier": "core",
        "language": "go",
        "owner": "commerce-core",
        "criticality": "gold",
        "version": "v1.9.3",
        "replicas": 2,
        "nodes": ["node-2", "node-4"],
        "endpoints": [("GET", "/api/v1/cart"), ("POST", "/api/v1/cart/items")],
        "layout": (2, 1),
    },
    {
        "name": "order-service",
        "display": "order-service",
        "namespace": "shop",
        "tier": "core",
        "language": "java",
        "owner": "commerce-core",
        "criticality": "platinum",
        "version": "v3.2.7",
        "replicas": 3,
        "nodes": ["node-1", "node-3", "node-5"],
        "endpoints": [("POST", "/api/v1/orders"), ("GET", "/api/v1/orders/{id}")],
        "layout": (2, 2),
    },
    {
        "name": "inventory-service",
        "display": "inventory-service",
        "namespace": "shop",
        "tier": "core",
        "language": "go",
        "owner": "supply-chain",
        "criticality": "gold",
        "version": "v1.5.1",
        "replicas": 2,
        "nodes": ["node-3", "node-6"],
        "endpoints": [("GET", "/api/v1/inventory/{sku}")],
        "layout": (2, 3),
    },
    {
        "name": "payment-gateway",
        "display": "payment-gateway",
        "namespace": "payments",
        "tier": "payment",
        "language": "go",
        "owner": "payments-backend",
        "criticality": "platinum",
        "version": "v3.2.1",
        "replicas": 3,
        "nodes": ["node-1", "node-2", "node-4"],
        "endpoints": [("POST", "/api/v1/pay")],
        "layout": (3, 0),
    },
    {
        "name": "payment-router",
        "display": "payment-router",
        "namespace": "payments",
        "tier": "channel",
        "language": "go",
        "owner": "payments-backend",
        "criticality": "platinum",
        "version": "v2.8.4",
        "replicas": 2,
        "nodes": ["node-2", "node-4"],
        "endpoints": [("POST", "/api/v1/route")],
        "layout": (4, 0),
    },
    {
        "name": "risk-control",
        "display": "risk-control",
        "namespace": "payments",
        "tier": "payment",
        "language": "java",
        "owner": "risk-platform",
        "criticality": "gold",
        "version": "v5.1.0",
        "replicas": 2,
        "nodes": ["node-3", "node-5"],
        "endpoints": [("POST", "/api/v1/risk/evaluate")],
        "layout": (3, 1),
    },
    {
        "name": "ledger-service",
        "display": "ledger-service",
        "namespace": "payments",
        "tier": "payment",
        "language": "java",
        "owner": "payments-backend",
        "criticality": "platinum",
        "version": "v4.6.2",
        "replicas": 2,
        "nodes": ["node-2", "node-5"],
        "endpoints": [("POST", "/api/v1/ledger/entry")],
        "layout": (3, 2),
    },
    {
        "name": "notification-service",
        "display": "notification-service",
        "namespace": "platform",
        "tier": "platform",
        "language": "python",
        "owner": "platform-sre",
        "criticality": "silver",
        "version": "v1.2.5",
        "replicas": 2,
        "nodes": ["node-4", "node-6"],
        "endpoints": [("POST", "/api/v1/notify")],
        "layout": (3, 3),
    },
    {
        "name": "batch-reporter",
        "display": "batch-reporter",
        "namespace": "platform",
        "tier": "platform",
        "language": "python",
        "owner": "data-platform",
        "criticality": "bronze",
        "version": "v0.9.8",
        "replicas": 1,
        "nodes": ["node-3"],
        "endpoints": [],
        "layout": (3, 4),
    },
]

EXTERNALS = [
    {
        "name": "alipay",
        "display": "Alipay Gateway",
        "domain": "api.alipay.com",
        "kind": "payment_channel",
        "provider": "Ant Group",
        "criticality": "platinum",
        "declared": True,
        "layout": (5, 0),
    },
    {
        "name": "wechatpay",
        "display": "WeChat Pay",
        "domain": "api.mch.weixin.qq.com",
        "kind": "payment_channel",
        "provider": "Tencent",
        "criticality": "platinum",
        "declared": True,
        "layout": (5, 1),
    },
    {
        "name": "unionpay",
        "display": "UnionPay Gateway",
        "domain": "gateway.95516.com",
        "kind": "payment_channel",
        "provider": "China UnionPay",
        "criticality": "gold",
        "declared": True,
        "layout": (5, 2),
    },
    {
        "name": "fraud-model",
        "display": "Fraud Scoring Model",
        "domain": "fraud-model.ml.svc.cluster.local",
        "kind": "ml_service",
        "provider": "in-house",
        "criticality": "gold",
        "declared": True,
        "layout": (4, 3),
    },
    {
        "name": "redis-cart",
        "display": "Cart Redis",
        "domain": "redis-cart.data.svc.cluster.local",
        "kind": "middleware",
        "provider": "in-house",
        "criticality": "gold",
        "declared": True,
        "layout": (4, 4),
    },
    {
        "name": "mysql-ledger",
        "display": "Ledger MySQL",
        "domain": "mysql-ledger.data.svc.cluster.local",
        "kind": "database",
        "provider": "in-house",
        "criticality": "platinum",
        "declared": True,
        "layout": (6, 1),
    },
    {
        "name": "mysql-inventory",
        "display": "Inventory MySQL",
        "domain": "mysql-inventory.data.svc.cluster.local",
        "kind": "database",
        "provider": "in-house",
        "criticality": "gold",
        "declared": True,
        "layout": (6, 3),
    },
    {
        "name": "promo-shadow-api",
        "display": "promo-flash.com API",
        "domain": "api.promo-flash.com",
        "kind": "third_party_api",
        "provider": "growth-vendor",
        "criticality": "silver",
        "declared": False,
        "layout": (5, 4),
    },
]

#: src, dst, method, path, baseline rps, baseline p99 (ms), baseline error rate.
#: ``payment_path`` edges feel the retry amplification; ``observed=False``
#: means the edge exists in the declared topology but produces no eBPF traffic.
EDGES = [
    # frontend fan-out
    {
        "src": "frontend",
        "dst": "checkout-service",
        "method": "POST",
        "path": "/api/v1/checkout",
        "rps": 420,
        "p99": 48,
        "err": 0.002,
    },
    {
        "src": "frontend",
        "dst": "cart-service",
        "method": "GET",
        "path": "/api/v1/cart",
        "rps": 900,
        "p99": 12,
        "err": 0.001,
    },
    {
        "src": "frontend",
        "dst": "order-service",
        "method": "GET",
        "path": "/api/v1/orders/{id}",
        "rps": 260,
        "p99": 28,
        "err": 0.001,
    },
    {
        "src": "frontend",
        "dst": "inventory-service",
        "method": "GET",
        "path": "/api/v1/inventory/{sku}",
        "rps": 700,
        "p99": 18,
        "err": 0.001,
    },
    # checkout fan-out
    {
        "src": "checkout-service",
        "dst": "cart-service",
        "method": "GET",
        "path": "/api/v1/cart",
        "rps": 380,
        "p99": 15,
        "err": 0.001,
    },
    {
        "src": "checkout-service",
        "dst": "inventory-service",
        "method": "GET",
        "path": "/api/v1/inventory/{sku}",
        "rps": 410,
        "p99": 20,
        "err": 0.001,
    },
    {
        "src": "checkout-service",
        "dst": "order-service",
        "method": "POST",
        "path": "/api/v1/orders",
        "rps": 300,
        "p99": 35,
        "err": 0.001,
    },
    {
        "src": "checkout-service",
        "dst": "payment-gateway",
        "method": "POST",
        "path": "/api/v1/pay",
        "rps": 420,
        "p99": 110,
        "err": 0.001,
        "payment_path": True,
    },
    {
        "src": "checkout-service",
        "dst": "notification-service",
        "method": "POST",
        "path": "/api/v1/notify",
        "rps": 120,
        "p99": 40,
        "err": 0.001,
    },
    # order fan-out
    {
        "src": "order-service",
        "dst": "inventory-service",
        "method": "GET",
        "path": "/api/v1/inventory/{sku}",
        "rps": 240,
        "p99": 17,
        "err": 0.001,
    },
    {
        "src": "order-service",
        "dst": "ledger-service",
        "method": "POST",
        "path": "/api/v1/ledger/entry",
        "rps": 230,
        "p99": 25,
        "err": 0.001,
        "payment_path": True,
    },
    {
        "src": "order-service",
        "dst": "notification-service",
        "method": "POST",
        "path": "/api/v1/notify",
        "rps": 90,
        "p99": 38,
        "err": 0.001,
        "observed": False,
    },
    # payment core
    {
        "src": "payment-gateway",
        "dst": "payment-router",
        "method": "POST",
        "path": "/api/v1/route",
        "rps": 410,
        "p99": 95,
        "err": 0.001,
        "payment_path": True,
    },
    {
        "src": "payment-gateway",
        "dst": "risk-control",
        "method": "POST",
        "path": "/api/v1/risk/evaluate",
        "rps": 400,
        "p99": 30,
        "err": 0.001,
        "payment_path": True,
    },
    {
        "src": "payment-gateway",
        "dst": "ledger-service",
        "method": "POST",
        "path": "/api/v1/ledger/entry",
        "rps": 405,
        "p99": 24,
        "err": 0.001,
        "payment_path": True,
    },
    # channels
    {
        "src": "payment-router",
        "dst": "alipay",
        "method": "POST",
        "path": "/gateway.do",
        "rps": 180,
        "p99": 220,
        "err": 0.002,
        "payment_path": True,
        "external": True,
        "p99_exp": 0.9,
        "err_slope": 0.03,
    },
    {
        "src": "payment-router",
        "dst": "wechatpay",
        "method": "POST",
        "path": "/pay/unifiedorder",
        "rps": 150,
        "p99": 190,
        "err": 0.002,
        "payment_path": True,
        "external": True,
        "p99_exp": 0.9,
        "err_slope": 0.026,
    },
    {
        "src": "payment-router",
        "dst": "unionpay",
        "method": "POST",
        "path": "/gateway",
        "rps": 80,
        "p99": 260,
        "err": 0.002,
        "payment_path": True,
        "external": True,
        "p99_exp": 0.9,
        "err_slope": 0.024,
    },
    # supporting dependencies
    {
        "src": "risk-control",
        "dst": "fraud-model",
        "method": "POST",
        "path": "/score",
        "rps": 390,
        "p99": 45,
        "err": 0.001,
        "payment_path": True,
        "external": True,
        "p99_exp": 1.0,
    },
    {
        "src": "cart-service",
        "dst": "redis-cart",
        "method": "GET",
        "path": "/cart",
        "rps": 880,
        "p99": 3,
        "err": 0.0005,
        "external": True,
        "p99_exp": 1.2,
    },
    {
        "src": "ledger-service",
        "dst": "mysql-ledger",
        "method": "SQL",
        "path": "/ledger",
        "rps": 400,
        "p99": 8,
        "err": 0.0005,
        "payment_path": True,
        "external": True,
        "p99_exp": 1.1,
    },
    {
        "src": "inventory-service",
        "dst": "mysql-inventory",
        "method": "SQL",
        "path": "/inventory",
        "rps": 700,
        "p99": 6,
        "err": 0.0005,
        "external": True,
        "p99_exp": 1.1,
    },
    {
        "src": "batch-reporter",
        "dst": "ledger-service",
        "method": "POST",
        "path": "/api/v1/ledger/entry",
        "rps": 60,
        "p99": 90,
        "err": 0.002,
        "steady": True,
    },
    # observed only after 00:31, never declared in the CMDB
    {
        "src": "checkout-service",
        "dst": "promo-shadow-api",
        "method": "POST",
        "path": "/api/v1/promo/apply",
        "rps": 0,
        "p99": 900,
        "err": 0.04,
        "external": True,
        "declared": False,
        "shadow": True,
    },
]

JOURNEYS = [
    {
        "name": "standard-checkout",
        "display": "Standard Checkout",
        "tier": "platinum",
        "owner": "commerce-core",
        "services": [
            "frontend",
            "checkout-service",
            "cart-service",
            "inventory-service",
            "order-service",
            "payment-gateway",
            "payment-router",
            "risk-control",
            "ledger-service",
        ],
        "conversion_impact": "18% drop in paid orders",
        "revenue_exposure": "CNY 2.4M/hour",
    },
    {
        "name": "subscription-renewal",
        "display": "Subscription Renewal",
        "tier": "gold",
        "owner": "payments-backend",
        "services": [
            "order-service",
            "payment-gateway",
            "ledger-service",
            "notification-service",
        ],
        "conversion_impact": "6% renewal failure",
        "revenue_exposure": "CNY 380k/hour",
    },
    {
        "name": "browse-and-cart",
        "display": "Browse & Cart",
        "tier": "silver",
        "owner": "storefront",
        "services": ["frontend", "cart-service", "inventory-service"],
        "conversion_impact": "p95 degraded by 340ms",
        "revenue_exposure": "CNY 120k/hour",
    },
]

CHANGES = [
    {
        "name": "CHG-2026-1101-07",
        "display": "checkout retry policy 2 -> 5",
        "service": "checkout-service",
        "change_type": "config",
        "applied_minute": 10,
        "risk": "high",
        "summary": "max_retries 2 -> 5; request timeout 500ms -> 2000ms",
        "detail": "ConfigMap checkout-retry-policy applied to checkout-service. Retries now cover 5 attempts and each attempt waits up to 2s, so a slow payment call is retried instead of failing fast.",
        "rollback": "kubectl apply -f checkout-retry-policy.v1.yaml && rollout restart deploy/checkout-service",
        "status": "applied",
    },
    {
        "name": "CHG-2026-1101-06",
        "display": "payment-gateway v3.2.1",
        "service": "payment-gateway",
        "change_type": "deployment",
        "applied_minute": -10,
        "risk": "low",
        "summary": "logging format only; no request-path change",
        "detail": "Deployment v3.2.1 changes structured log field names and bumps the base image digest. Diff contains no handler, retry, timeout, or connection-pool changes.",
        "rollback": "kubectl rollout undo deploy/payment-gateway",
        "status": "applied",
    },
]

ALERTS = [
    {
        "name": "ALT-2026-1102-001",
        "display": "payment-gateway SLO burn rate 14.2x",
        "target": "payment-gateway",
        "target_type": "svc.service",
        "severity": "P1",
        "status": "firing",
        "fired_minute": 40,
        "signal": "slo_burn_rate",
        "value": "14.2x",
        "summary": "P99 latency 2.6s against an 800ms SLO for 10 consecutive minutes.",
    },
    {
        "name": "ALT-2026-1102-002",
        "display": "node-3 CPU saturation 92%",
        "target": "node-3",
        "target_type": "infra.node",
        "severity": "P3",
        "status": "firing",
        "fired_minute": 25,
        "signal": "node_cpu_utilization",
        "value": "92%",
        "summary": "Sustained CPU pressure on node-3 after the nightly batch reporter started.",
    },
]

INCIDENT = {
    "name": INCIDENT_ID,
    "display": "Payment P99 SLO breach",
    "severity": "P1",
    "status": "investigating",
    "started_minute": 40,
    "detected_minute": 40,
    "victim": VICTIM_SERVICE,
    "owner": "payments-oncall",
    "summary": "payment-gateway P99 is 2.6s against an 800ms SLO; checkout success rate is down 3.4 percentage points.",
    "impact": "Standard Checkout conversion is down 18%; two payment channels are returning 5xx.",
}

TIMELINE = [
    {
        "minute": -10,
        "kind": "change",
        "title": "payment-gateway v3.2.1 deployed",
        "detail": "Logging format only (red herring).",
        "ref": "CHG-2026-1101-06",
    },
    {
        "minute": 5,
        "kind": "business",
        "title": "Flash Sale campaign active",
        "detail": "Traffic multiplier 3.2x begins to ramp.",
        "ref": "standard-checkout",
    },
    {
        "minute": 10,
        "kind": "change",
        "title": "checkout retry policy changed",
        "detail": "max_retries 2 -> 5, timeout 500ms -> 2000ms.",
        "ref": "CHG-2026-1101-07",
    },
    {
        "minute": 11,
        "kind": "signal",
        "title": "checkout outbound RPS anomaly",
        "detail": "Retry amplification appears before payment latency.",
        "ref": "checkout-service",
    },
    {
        "minute": 14,
        "kind": "signal",
        "title": "payment-gateway p99 breach begins",
        "detail": "Inbound p99 rises past 200ms with 1.6x load.",
        "ref": "payment-gateway",
    },
    {
        "minute": 16,
        "kind": "signal",
        "title": "payment-router latency follows",
        "detail": "Downstream router p99 crosses 150ms.",
        "ref": "payment-router",
    },
    {
        "minute": 19,
        "kind": "signal",
        "title": "Alipay 5xx rate rises",
        "detail": "Channel error rate reaches 19%.",
        "ref": "alipay",
    },
    {
        "minute": 20,
        "kind": "noise",
        "title": "node-3 CPU saturation",
        "detail": "batch-reporter saturates node-3 (not on the payment path).",
        "ref": "node-3",
    },
    {
        "minute": 30,
        "kind": "business",
        "title": "Flash Sale traffic peak",
        "detail": "3.2x traffic meets 2.5x retry amplification.",
        "ref": "standard-checkout",
    },
    {
        "minute": 40,
        "kind": "alert",
        "title": "SLO burn-rate alert fires",
        "detail": "payment-gateway P99 2.6s, burn rate 14.2x.",
        "ref": "ALT-2026-1102-001",
    },
    {
        "minute": 42,
        "kind": "incident",
        "title": "INC-2026-1102-01 opened",
        "detail": "Payment on-call starts graph investigation.",
        "ref": INCIDENT_ID,
    },
]

# Metric curve parameters. The retry multiplier and traffic multiplier are
# separated so the graph shows *why* load grows: configuration first, demand
# second.
RETRY_START = 10
SALE_START = 30
LATENCY_LAG = 3


def service(name: str) -> dict[str, Any]:
    for item in SERVICES:
        if item["name"] == name:
            return item
    raise KeyError(name)


def external(name: str) -> dict[str, Any]:
    for item in EXTERNALS:
        if item["name"] == name:
            return item
    raise KeyError(name)


def edge_key(edge: dict[str, Any]) -> str:
    return f"{edge['src']}->{edge['dst']}"


def _rng(*parts: Any) -> random.Random:
    seed = "|".join(str(part) for part in parts)
    return random.Random(seed)


def retry_amplification(minute: int) -> float:
    if minute < RETRY_START:
        return 1.0
    if minute < SALE_START:
        return 1.55 + (minute - RETRY_START) * (0.95 / 19.0)
    ramp = min(1.0, (minute - SALE_START) / 5.0)
    return 2.50 + ramp * (8.00 - 2.50)


def traffic_multiplier(minute: int) -> float:
    if minute < SALE_START:
        return 1.0
    return 1.0 + min(1.0, (minute - SALE_START) / 5.0) * 2.2


def _load(edge: dict[str, Any], minute: int) -> float:
    if edge.get("steady"):
        return 1.0
    if edge.get("shadow"):
        return 1.0
    if edge.get("payment_path"):
        # retry_amplification already folds in the flash-sale traffic; keep the
        # two multipliers separate in the narrative, not multiplied twice here.
        return retry_amplification(minute)
    return traffic_multiplier(minute)


def _p99(edge: dict[str, Any], minute: int) -> float:
    base = float(edge["p99"])
    if edge.get("steady"):
        load = 1.0
        lagged = minute
    else:
        lagged = minute - LATENCY_LAG
        load = max(1.0, _load(edge, lagged))
    exponent = float(edge.get("p99_exp", 1.45))
    value = base * (load**exponent)
    # checkout's own inbound latency is dominated by its downstream call.
    if edge["src"] == "frontend" and edge["dst"] == "checkout-service":
        payment = _payment_latency(minute)
        value = payment * 0.85 + 40.0
    return min(value, base * 40.0)


def _payment_latency(minute: int) -> float:
    edge = next(
        item
        for item in EDGES
        if item["dst"] == "payment-gateway" and item["src"] == "checkout-service"
    )
    return _p99(edge, minute)


def _error(edge: dict[str, Any], minute: int) -> float:
    base = float(edge["err"])
    if edge.get("steady"):
        load = 1.0
    else:
        lagged = max(0, minute - LATENCY_LAG)
        load = max(1.0, _load(edge, lagged))
    slope = float(edge.get("err_slope", 0.0018))
    value = base + max(0.0, load - 1.6) * slope
    return min(value, 0.35)


def _pods(service_name: str) -> list[dict[str, Any]]:
    item = service(service_name)
    namespace = item["namespace"]
    ns_index = next(i for i, ns in enumerate(NAMESPACES) if ns["name"] == namespace) + 1
    svc_index = next(i for i, svc in enumerate(SERVICES) if svc["name"] == service_name) + 1
    pods = []
    for index in range(item["replicas"]):
        digest = hashlib.md5(f"{service_name}:{index}".encode()).hexdigest()[:5]
        pods.append(
            {
                "name": f"{service_name}-{digest}-{index}",
                "service": service_name,
                "namespace": namespace,
                "node": item["nodes"][index % len(item["nodes"])],
                "ip": f"10.42.{ns_index}.{svc_index * 10 + index}",
                "index": index,
            }
        )
    return pods


_PODS = {item["name"]: _pods(item["name"]) for item in SERVICES}


def pods_for(service_name: str) -> list[dict[str, Any]]:
    return _PODS[service_name]


def node_for(service_name: str, index: int = 0) -> str:
    return pods_for(service_name)[index % len(pods_for(service_name))]["node"]


def domain_for(service_name: str) -> str:
    item = service(service_name)
    return f"{service_name}.{item['namespace']}.svc.cluster.local"


def _endpoint_for(edge: dict[str, Any]) -> str | None:
    if edge.get("external") or edge.get("shadow") or edge.get("observed") is False:
        return None
    dest = service(edge["dst"])
    path = edge["path"]
    if any(path == endpoint_path for _, endpoint_path in dest["endpoints"]):
        return path
    return dest["endpoints"][0][1]


def declared_topology() -> dict[str, Any]:
    pods = [pod for item in SERVICES for pod in pods_for(item["name"])]
    workloads = []
    for item in SERVICES:
        workloads.append(
            {
                "name": f"{item['name']}-deploy",
                "service": item["name"],
                "namespace": item["namespace"],
                "kind": "CronJob" if item["name"] == "batch-reporter" else "Deployment",
                "replicas": item["replicas"],
                "ready": item["replicas"],
                "image": f"registry.example.com/{item['namespace']}/{item['name']}:{item['version']}",
            }
        )
    edges = []
    for edge in EDGES:
        item = {
            "src": edge["src"],
            "dst": edge["dst"],
            "method": edge["method"],
            "path": edge["path"],
            "declared": edge.get("declared", True),
            "observed": edge.get("observed", True) and edge.get("rps", 0) > 0,
            "external": bool(edge.get("external") or edge.get("shadow")),
            "shadow": bool(edge.get("shadow")),
        }
        edges.append(item)
    return {
        "cluster": CLUSTER,
        "nodes": NODES,
        "namespaces": NAMESPACES,
        "services": SERVICES,
        "externals": EXTERNALS,
        "pods": pods,
        "workloads": workloads,
        "journeys": JOURNEYS,
        "edges": edges,
        "changes": [{key: value for key, value in change.items()} for change in CHANGES],
        "alerts": ALERTS,
        "incident": INCIDENT,
        "timeline": TIMELINE,
    }


FLOW_COLUMNS = [
    "time",
    "pod_service_0",
    "pod_0",
    "pod_ns_0",
    "pod_node_0",
    "pod_service_1",
    "pod_1",
    "pod_ns_1",
    "pod_node_1",
    "request_domain",
    "endpoint",
    "request_type",
    "l7_protocol_str",
    "tap_side",
    "observation_point",
    "is_internet_0",
    "is_internet_1",
    "request",
    "response",
    "rrt_sum",
    "rrt_count",
    "rrt_p99",
    "client_error",
    "server_error",
    "byte_tx",
    "byte_rx",
]


def _flow_row(edge: dict[str, Any], minute: int) -> dict[str, Any] | None:
    if edge.get("observed") is False:
        return None
    if edge.get("shadow") and minute < 31:
        return None

    src = edge["src"]
    dst = edge["dst"]
    src_pod = pods_for(src)[0]
    if edge.get("shadow"):
        rps = 120.0 * min(1.0, (minute - 30) / 4.0)
        p99 = 900.0 + 60.0 * math.sin(minute)
        err = 0.04
    elif edge.get("external"):
        load = max(1.0, _load(edge, minute))
        rps = float(edge["rps"]) * load
        p99 = _p99(edge, minute)
        err = _error(edge, minute)
    else:
        load = max(1.0, _load(edge, minute))
        rps = float(edge["rps"]) * load
        if edge["src"] == "frontend" and edge["dst"] == "checkout-service":
            rps = float(edge["rps"]) * traffic_multiplier(minute)
        p99 = _p99(edge, minute)
        err = _error(edge, minute)

    noise = _rng(src, dst, minute)
    if edge["src"] == "frontend" and edge["dst"] == "checkout-service":
        payment_edge = next(
            item
            for item in EDGES
            if item["src"] == "checkout-service" and item["dst"] == "payment-gateway"
        )
        err = max(err, _error(payment_edge, minute) * 0.9)
    rps *= noise.uniform(0.94, 1.06)
    p99 *= noise.uniform(0.95, 1.05)
    err = max(0.0, err * noise.uniform(0.9, 1.1))
    requests = max(0, int(round(rps * 60)))
    if requests == 0:
        return None
    avg_ms = max(0.5, p99 * 0.52)
    rrt_sum = int(requests * avg_ms * 1000)
    server_errors = int(requests * err * 0.82)
    client_errors = int(requests * err * 0.18)

    if edge.get("external") or edge.get("shadow"):
        dst_pod = {"name": "", "namespace": "", "node": ""}
        dst_domain = external(dst)["domain"]
        is_internet_1 = 1
    else:
        dst_pod = pods_for(dst)[0]
        dst_domain = domain_for(dst)
        is_internet_1 = 0

    return {
        "time": SCENARIO_START + minute * 60,
        "pod_service_0": src,
        "pod_0": src_pod["name"],
        "pod_ns_0": src_pod["namespace"],
        "pod_node_0": src_pod["node"],
        "pod_service_1": "" if (edge.get("external") or edge.get("shadow")) else dst,
        "pod_1": dst_pod["name"],
        "pod_ns_1": dst_pod["namespace"],
        "pod_node_1": dst_pod["node"],
        "request_domain": dst_domain,
        "endpoint": edge["path"],
        "request_type": edge["method"],
        "l7_protocol_str": "HTTP" if edge["method"] != "SQL" else "MySQL",
        "tap_side": "server",
        "observation_point": "c-app",
        "is_internet_0": 0,
        "is_internet_1": is_internet_1,
        "request": requests,
        "response": requests,
        "rrt_sum": rrt_sum,
        "rrt_count": requests,
        "rrt_p99": round(p99, 3),
        "client_error": client_errors,
        "server_error": server_errors,
        "byte_tx": requests * 780,
        "byte_rx": requests * 1420,
    }


def _node_metrics(minute: int) -> list[dict[str, Any]]:
    rows = []
    payment_load = max(1.0, _load(next(e for e in EDGES if e.get("payment_path")), minute))
    for node in NODES:
        cpu = float(node["base_cpu"])
        if node["name"] == "node-3":
            cpu = 0.36 + min(1.0, max(0.0, (minute - 19) / 4.0)) * 0.56
        elif node["name"] in {"node-1", "node-2"} and minute >= SALE_START:
            cpu += 0.18 * min(1.0, (payment_load - 1.0) / 7.0)
        elif minute >= SALE_START:
            cpu += 0.05
        if minute < 20:
            cpu += _rng(node["name"], minute).uniform(-0.02, 0.02)
        rows.append(
            {
                "time": SCENARIO_START + minute * 60,
                "pod_node": node["name"],
                "cpu_utilization": round(min(0.99, cpu), 4),
                "memory_utilization": round(0.55 + (0.17 if node["name"] == "node-3" else 0.02), 4),
                "pod_count": sum(
                    1
                    for svc in SERVICES
                    for pod in pods_for(svc["name"])
                    if pod["node"] == node["name"]
                ),
                "net_rx_bytes": int(1_200_000 * (1.0 + (payment_load - 1.0) * 0.08)),
            }
        )
    return rows


def generate_replay(output_dir: Path | None = None) -> dict[str, Any]:
    target = Path(output_dir or REPLAY_DIR)
    target.mkdir(parents=True, exist_ok=True)

    flow_path = target / "l7_flow_log.csv"
    flow_rows: list[dict[str, Any]] = []
    for minute in range(WINDOW_MINUTES):
        for edge in EDGES:
            row = _flow_row(edge, minute)
            if row is not None:
                flow_rows.append(row)

    with flow_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FLOW_COLUMNS)
        writer.writeheader()
        writer.writerows(flow_rows)

    node_path = target / "node_metrics.csv"
    node_rows = [row for minute in range(WINDOW_MINUTES) for row in _node_metrics(minute)]
    with node_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "time",
                "pod_node",
                "cpu_utilization",
                "memory_utilization",
                "pod_count",
                "net_rx_bytes",
            ],
        )
        writer.writeheader()
        writer.writerows(node_rows)

    topology = declared_topology()
    write_json(target / "declared_topology.json", topology)
    write_json(
        target / "scenario.json",
        {
            "scenario": "checkout-retry-storm",
            "start": iso(SCENARIO_START),
            "end": iso(SCENARIO_START + WINDOW_MINUTES * 60),
            "window_minutes": WINDOW_MINUTES,
            "flow_rows": len(flow_rows),
            "node_rows": len(node_rows),
            "expected_root_cause": "checkout-service",
            "expected_victim": VICTIM_SERVICE,
            "expected_findings": [
                "shadow-dependency",
                "declared-but-unobserved",
                "node-3 red herring",
            ],
        },
    )
    return {
        "flow_rows": len(flow_rows),
        "node_rows": len(node_rows),
        "flow_path": str(flow_path),
        "node_path": str(node_path),
        "topology_path": str(target / "declared_topology.json"),
    }


def scenario_time(minute: int) -> str:
    return clock(ts(minute))


def scenario_iso(minute: int) -> str:
    return iso(ts(minute))
