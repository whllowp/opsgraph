"""Local console API and static file server for the OpsGraph UI."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .analysis import GraphView, analyze
from .config import (
    ARTIFACT_DIR,
    CONSOLE_HOST,
    CONSOLE_PORT,
    PROJECT_ROOT,
    UMODEL_ADDR,
    WORKSPACE,
)
from .ontology import ENTITY_SETS, LINKS
from .umodel_client import UModelClient
from .util import read_json, write_json

CONSOLE_DIR = PROJECT_ROOT / "console"


def _tier_x(tier: str) -> float:
    return {
        "journey": 48.0,
        "edge": 185.0,
        "core": 345.0,
        "platform": 480.0,
        "payment": 600.0,
        "support": 690.0,
        "channel": 790.0,
        "data": 920.0,
    }.get(tier, 600.0)


def _external_tier(entity: dict[str, Any]) -> str:
    kind = str(entity.get("kind") or "")
    if kind in {"payment_channel"}:
        return "channel"
    if kind in {"database", "middleware"}:
        return "data"
    if kind in {"ml_service"}:
        return "support"
    return "channel"


def _node_entity(entity: dict[str, Any]) -> dict[str, Any]:
    etype = str(entity.get("__entity_type__") or "")
    name = str(entity.get("display_name") or entity.get("id") or "")
    status = str(entity.get("status") or "unknown")
    role = str(entity.get("role") or "")
    radius = 18.0
    tier = "core"
    if etype == "svc.service":
        tier = str(entity.get("tier") or "core")
        radius = 19.0 if entity.get("criticality") == "platinum" else 16.0
        if status == "critical":
            radius += 1.5
    elif etype == "svc.external_service":
        tier = _external_tier(entity)
        radius = 13.0
        status = "shadow" if entity.get("shadow") else status
    elif etype == "biz.journey":
        tier = "journey"
        status = "impacted" if entity.get("impacted") else "healthy"
        radius = 10.0
    elif etype == "ops.incident":
        tier = "ops"
        radius = 11.0
    elif etype == "ops.change":
        tier = "ops"
        radius = 8.0
    elif etype == "ops.alert":
        tier = "ops"
        radius = 8.0
    return {
        "id": entity.get("__entity_id__"),
        "name": name,
        "type": etype,
        "status": status,
        "role": role,
        "tier": tier,
        "radius": radius,
        "criticality": entity.get("criticality"),
        "metrics": {
            "qps": entity.get("qps"),
            "p99_ms": entity.get("latency_p99_ms"),
            "baseline_p99_ms": entity.get("baseline_p99_ms"),
            "delta_pct": entity.get("p99_delta_pct"),
            "error_rate": entity.get("error_rate"),
            "anomaly": entity.get("anomaly_score"),
            "outbound_amp": entity.get("outbound_amp"),
            "onset_ts": entity.get("onset_ts"),
            "onset_signal": entity.get("onset_signal"),
        },
    }


def _layout(nodes: list[dict[str, Any]]) -> None:
    order = {
        "frontend-web": 0,
        "checkout-service": 1,
        "cart-service": 2,
        "order-service": 3,
        "inventory-service": 4,
        "notification-service": 5,
        "batch-reporter": 6,
        "payment-gateway": 0,
        "risk-control": 1,
        "ledger-service": 2,
        "payment-router": 0,
        "Alipay Gateway": 1,
        "WeChat Pay": 2,
        "UnionPay Gateway": 3,
        "Fraud Scoring Model": 4,
        "Cart Redis": 5,
        "Ledger MySQL": 6,
        "Inventory MySQL": 7,
        "promo-flash.com API": 8,
        "Standard Checkout": 0,
        "Subscription Renewal": 1,
        "Browse & Cart": 2,
    }
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        by_tier.setdefault(node["tier"], []).append(node)
    for tier, items in by_tier.items():
        items.sort(key=lambda item: (order.get(item["name"], 50), item["name"]))
        if tier == "ops":
            for index, item in enumerate(items):
                item["x"] = 940.0
                item["y"] = 640.0 + index * 34.0
            continue
        spacing = 82.0 if len(items) <= 6 else 64.0
        total = (len(items) - 1) * spacing
        for index, item in enumerate(items):
            item["x"] = _tier_x(tier)
            item["y"] = 400.0 - total / 2 + index * spacing


def build_console_state(
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    umodel_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    visible_types = {
        "svc.service",
        "svc.external_service",
        "biz.journey",
    }
    entities = [
        item
        for item in snapshot.get("entities", [])
        if item.get("__entity_type__") in visible_types
    ]
    nodes = [_node_entity(item) for item in entities]
    by_id = {str(item.get("__entity_id__")): item for item in entities}
    _layout(nodes)

    visible_ids = set(by_id)
    edges = []
    for relation in snapshot.get("relations", []):
        src = str(relation.get("__src_entity_id__"))
        dst = str(relation.get("__dest_entity_id__"))
        if src not in visible_ids or dst not in visible_ids:
            continue
        rtype = str(relation.get("__relation_type__") or "")
        observed = relation.get("observed")
        declared = relation.get("declared")
        shadow = bool(relation.get("shadow"))
        p99 = float(relation.get("latency_p99_ms") or 0)
        err = float(relation.get("error_rate") or 0)
        anomalous = rtype == "calls" and (
            float(relation.get("p99_delta_pct") or 0) >= 50 or err >= 0.01
        )
        edges.append(
            {
                "id": f"{src}:{rtype}:{dst}",
                "src": src,
                "dst": dst,
                "type": rtype,
                "rps": float(relation.get("rps") or 0),
                "p99_ms": p99,
                "baseline_p99_ms": float(relation.get("baseline_p99_ms") or 0),
                "delta_pct": float(relation.get("p99_delta_pct") or 0),
                "error_rate": err,
                "observed": observed,
                "declared": declared,
                "shadow": shadow,
                "anomalous": anomalous,
                "onset_ts": relation.get("onset_ts"),
                "evidence": relation.get("evidence"),
            }
        )

    # Focus edges by default: call graph + business + incident overlay.
    focus_types = {"calls", "depends_on", "affects"}
    counts: dict[str, int] = {}
    for item in snapshot.get("entities", []):
        key = str(item.get("__entity_type__"))
        counts[key] = counts.get(key, 0) + 1
    queries = [
        {
            "label": "在 UModel 中定位受害服务",
            "spl": ".entity with(domain='svc', name='svc.service', query='payment-gateway') | project display_name,status,qps,latency_p99_ms,p99_delta_pct,role",
        },
        {
            "label": "读取 checkout 的调用边（携带 eBPF 证据）",
            "spl": f".topo | graph-call getDirectRelations([(:\\\"svc@svc.service\\\" {{__entity_id__: '{next((item.get('__entity_id__') for item in snapshot.get('entities', []) if item.get('display_name') == 'checkout-service'), '<checkout-id>')}'}}))]) | with(__relation_type__='calls') | project display_name,rps,latency_p99_ms,p99_delta_pct,onset_ts",
        },
        {
            "label": "查看模型中的实体集与关系",
            "spl": ".umodel with(kind='entity_set') | sort name | limit 20",
        },
        {
            "label": "用 Cypher 读取完整证据图",
            "spl": ".topo | graph-call cypher(`MATCH (src)-[r]->(dest) RETURN src, r AS relation, dest LIMIT 50`)",
        },
    ]
    return {
        "meta": {
            "workspace": snapshot.get("workspace"),
            "window": snapshot.get("window"),
            "generated_at": analysis.get("generated_at"),
            "graph_counts": snapshot.get("counts"),
            "type_counts": counts,
            "model": {"entity_sets": len(ENTITY_SETS), "links": len(LINKS)},
            "umodel": umodel_info or {"addr": UMODEL_ADDR, "connected": False},
            "source": "UModel Query Service + DeepFlow l7_flow_log",
        },
        "nodes": nodes,
        "edges": edges,
        "focus_types": sorted(focus_types),
        "incident": next(
            (
                item
                for item in snapshot.get("entities", [])
                if item.get("__entity_type__") == "ops.incident"
            ),
            {},
        ),
        "analysis": analysis,
        "queries": queries,
    }


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "OpsGraphConsole/0.1"
    snapshot: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    umodel_info: dict[str, Any] = {"addr": UMODEL_ADDR, "connected": False}
    lock = threading.Lock()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[console] {self.address_string()} {fmt % args}")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _load(self) -> bool:
        cls = type(self)
        if cls.snapshot is None or cls.analysis is None:
            snapshot_path = ARTIFACT_DIR / "snapshot.json"
            analysis_path = ARTIFACT_DIR / "analysis.json"
            if not snapshot_path.exists() or not analysis_path.exists():
                return False
            cls.snapshot = read_json(snapshot_path)
            cls.analysis = read_json(analysis_path)
        return True

    def _state(self) -> dict[str, Any]:
        cls = type(self)
        assert cls.snapshot is not None and cls.analysis is not None
        return build_console_state(cls.snapshot, cls.analysis, cls.umodel_info)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json({"status": "ok"})
            return
        if path == "/api/state":
            with self.lock:
                if not self._load():
                    self._json(
                        {"error": "no artifacts; run `python -m opsgraph demo` first"},
                        503,
                    )
                    return
                self._json(self._state())
            return
        if path == "/api/report":
            report = ARTIFACT_DIR / "incident-report.md"
            if not report.exists():
                self._json({"error": "report not found"}, 404)
                return
            body = report.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/entity":
            from urllib.parse import parse_qs

            query = parse_qs(parsed.query)
            eid = (query.get("id") or [""])[0]
            with self.lock:
                if not self._load():
                    self._json({"error": "no artifacts"}, 503)
                    return
                entity = next(
                    (
                        item
                        for item in type(self).snapshot.get("entities", [])
                        if str(item.get("__entity_id__")) == eid
                    ),
                    None,
                )
                relations = [
                    item
                    for item in type(self).snapshot.get("relations", [])
                    if str(item.get("__src_entity_id__")) == eid
                    or str(item.get("__dest_entity_id__")) == eid
                ]
            if not entity:
                self._json({"error": "entity not found"}, 404)
                return
            self._json({"entity": entity, "relations": relations})
            return
        self._static(path)

    def _static(self, path: str) -> None:
        if path in {"", "/"}:
            target = CONSOLE_DIR / "index.html"
        else:
            target = (CONSOLE_DIR / path.lstrip("/")).resolve()
            if (
                CONSOLE_DIR.resolve() not in target.parents
                and target != CONSOLE_DIR.resolve()
            ):
                self._json({"error": "forbidden"}, 403)
                return
        if not target.exists() or not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = target.read_bytes()
        content_type = (
            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        )
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        if parsed.path == "/api/analyze":
            with self.lock:
                if not self._load():
                    self._json({"error": "no snapshot"}, 503)
                    return
                analysis = analyze(type(self).snapshot)
                type(self).analysis = analysis
                write_json(ARTIFACT_DIR / "snapshot.json", type(self).snapshot)
                write_json(ARTIFACT_DIR / "analysis.json", analysis)
                self._json(self._state())
            return

        if parsed.path == "/api/query":
            spl = str(payload.get("spl") or "")
            if not spl:
                self._json({"error": "spl is required"}, 400)
                return
            try:
                client = UModelClient(UMODEL_ADDR, WORKSPACE)
                rows = client.query(spl)
                self._json({"rows": rows, "count": len(rows)})
            except Exception as exc:
                self._json({"error": str(exc)}, 502)
            return

        if parsed.path == "/api/simulate":
            with self.lock:
                if not self._load():
                    self._json({"error": "no snapshot"}, 503)
                    return
                remove = set(payload.get("remove") or [])
                snapshot = type(self).snapshot
                graph = GraphView(snapshot)
                impacted: list[str] = []
                for service in remove:
                    if service in graph.services:
                        impacted.extend(graph.reachable(service))
                impacted = sorted(
                    set(name for name in impacted if name in graph.services) - remove
                )
                services = []
                for name in impacted:
                    entity = graph.services.get(name, {})
                    services.append(
                        {
                            "service": name,
                            "qps": entity.get("qps"),
                            "p99_ms": entity.get("latency_p99_ms"),
                            "criticality": entity.get("criticality"),
                        }
                    )
                self._json(
                    {
                        "removed": sorted(remove),
                        "impacted": services,
                        "count": len(services),
                    }
                )
            return

        self._json({"error": "not found"}, 404)


def serve(host: str = CONSOLE_HOST, port: int = CONSOLE_PORT) -> None:
    handler = ConsoleHandler
    try:
        info = UModelClient(UMODEL_ADDR, WORKSPACE).health()
        handler.umodel_info = {
            "addr": UMODEL_ADDR,
            "connected": info.get("status") == "ok",
            "provider": info.get("graphstore", {}).get("provider"),
        }
    except Exception:
        handler.umodel_info = {"addr": UMODEL_ADDR, "connected": False}
    server = ThreadingHTTPServer((host, port), handler)
    print(f"OpsGraph console: http://{host}:{port}")
    print("  API: /api/state  /api/analyze  /api/query  /api/simulate  /api/report")
    server.serve_forever()
