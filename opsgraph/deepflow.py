"""DeepFlow adapters.

Two sources share one normalized record shape:

* ``replay`` reads a DeepFlow-shaped CSV aggregate (the default, deterministic
  demo path);
* ``deepflow`` calls the DeepFlow querier ``/v1/query/`` API with an
  ``l7_flow_log`` aggregation. The SQL intentionally uses DeepFlow's universal
  tags (``pod_service``, ``pod``, ``pod_node``, ``request_domain``) so the same
  graph builder works against a live agent deployment.

Only L7 flow telemetry is required for graph construction. Node metrics are
optional and can come from the replay CSV or a Prometheus-compatible endpoint.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from .config import REPLAY_DIR

NUMERIC_FIELDS = (
    "time",
    "request",
    "response",
    "rrt_sum",
    "rrt_count",
    "rrt_p99",
    "client_error",
    "server_error",
    "byte_tx",
    "byte_rx",
    "is_internet_0",
    "is_internet_1",
)


@dataclass
class FlowRecord:
    time: int
    client_service: str
    client_pod: str
    client_namespace: str
    client_node: str
    server_service: str
    server_pod: str
    server_namespace: str
    server_node: str
    request_domain: str
    endpoint: str
    method: str
    protocol: str
    requests: int
    responses: int
    rrt_sum_us: int
    rrt_count: int
    p99_ms: float
    client_errors: int
    server_errors: int
    byte_tx: int
    byte_rx: int
    external: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def error_rate(self) -> float:
        total = max(1, self.requests)
        return (self.client_errors + self.server_errors) / total

    @property
    def rps(self) -> float:
        return self.requests / 60.0

    @property
    def avg_ms(self) -> float:
        return self.rrt_sum_us / max(1, self.rrt_count) / 1000.0

    @property
    def edge_key(self) -> str:
        return f"{self.client_service}->{self.server_service}:{self.method} {self.endpoint}"


def _to_int(value: Any, default: int = 0) -> int:
    if value in (None, "", "null"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "null"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _service_from_pod(pod: str) -> str:
    """Best-effort fallback when DeepFlow has no pod_service tag."""

    if not pod:
        return ""
    parts = pod.split("-")
    if len(parts) >= 3 and len(parts[-1]) <= 2:
        return "-".join(parts[:-2])
    return pod


def _service_from_domain(domain: str) -> str:
    if not domain:
        return ""
    return domain.split(".")[0]


def normalize_record(row: dict[str, Any]) -> FlowRecord | None:
    values = {key: row.get(key) for key in row}
    for key in NUMERIC_FIELDS:
        if key in values:
            values[key] = _to_int(values[key]) if key != "rrt_p99" else _to_float(values[key])
    requests = _to_int(values.get("request"))
    if requests <= 0:
        return None

    client = str(values.get("pod_service_0") or _service_from_pod(str(values.get("pod_0") or "")))
    server = str(
        values.get("pod_service_1") or _service_from_domain(str(values.get("request_domain") or ""))
    )
    if not client or not server:
        return None
    external = bool(_to_int(values.get("is_internet_1"))) or not values.get("pod_service_1")
    p99 = _to_float(values.get("rrt_p99"))
    if p99 <= 0:
        # Older exports may only carry the mean; scale it to a p99 estimate.
        p99 = (
            _to_float(values.get("rrt_sum"))
            / max(1, _to_int(values.get("rrt_count")))
            / 1000.0
            * 1.9
        )

    return FlowRecord(
        time=_to_int(values.get("time")),
        client_service=client,
        client_pod=str(values.get("pod_0") or ""),
        client_namespace=str(values.get("pod_ns_0") or ""),
        client_node=str(values.get("pod_node_0") or ""),
        server_service=server,
        server_pod=str(values.get("pod_1") or ""),
        server_namespace=str(values.get("pod_ns_1") or ""),
        server_node=str(values.get("pod_node_1") or ""),
        request_domain=str(values.get("request_domain") or ""),
        endpoint=str(values.get("endpoint") or "/"),
        method=str(values.get("request_type") or values.get("method") or "GET").upper(),
        protocol=str(values.get("l7_protocol_str") or "HTTP"),
        requests=requests,
        responses=_to_int(values.get("response"), requests),
        rrt_sum_us=_to_int(values.get("rrt_sum")),
        rrt_count=_to_int(values.get("rrt_count"), requests),
        p99_ms=p99,
        client_errors=_to_int(values.get("client_error")),
        server_errors=_to_int(values.get("server_error")),
        byte_tx=_to_int(values.get("byte_tx")),
        byte_rx=_to_int(values.get("byte_rx")),
        external=external,
        raw=values,
    )


@dataclass
class NodeMetric:
    time: int
    node: str
    cpu: float
    memory: float
    pod_count: int
    net_rx_bytes: int


def load_replay_flows(path: str | Path | None = None) -> list[FlowRecord]:
    target = Path(path or REPLAY_DIR / "l7_flow_log.csv")
    records: list[FlowRecord] = []
    with target.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            record = normalize_record(row)
            if record is not None:
                records.append(record)
    return records


def load_node_metrics(path: str | Path | None = None) -> list[NodeMetric]:
    target = Path(path or REPLAY_DIR / "node_metrics.csv")
    rows: list[NodeMetric] = []
    with target.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                NodeMetric(
                    time=_to_int(row.get("time")),
                    node=str(row.get("pod_node") or ""),
                    cpu=_to_float(row.get("cpu_utilization")),
                    memory=_to_float(row.get("memory_utilization")),
                    pod_count=_to_int(row.get("pod_count")),
                    net_rx_bytes=_to_int(row.get("net_rx_bytes")),
                )
            )
    return rows


def load_declared_topology(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path or REPLAY_DIR / "declared_topology.json")
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


L7_SQL_TEMPLATE = """
SELECT time,
       pod_service_0, pod_0, pod_ns_0, pod_node_0,
       pod_service_1, pod_1, pod_ns_1, pod_node_1,
       request_domain, endpoint, request_type, l7_protocol_str,
       SUM(request) AS request,
       SUM(response) AS response,
       SUM(rrt_sum) AS rrt_sum,
       SUM(rrt_count) AS rrt_count,
       quantile(0.99)(rrt) AS rrt_p99,
       SUM(client_error) AS client_error,
       SUM(server_error) AS server_error,
       SUM(byte_tx) AS byte_tx,
       SUM(byte_rx) AS byte_rx
FROM l7_flow_log
WHERE time >= {start} AND time <= {end}
  AND tap_side = 'server'
GROUP BY time, pod_service_0, pod_0, pod_ns_0, pod_node_0,
         pod_service_1, pod_1, pod_ns_1, pod_node_1,
         request_domain, endpoint, request_type, l7_protocol_str
ORDER BY time ASC
LIMIT 200000
""".strip()


class DeepFlowClient:
    """Thin DeepFlow querier client.

    ``base_url`` is the DeepFlow server root, for example
    ``http://deepflow-server.deepflow.svc:20416``. The API is documented at
    ``POST /v1/query/``; ``db=flow_log`` selects the flow log database.
    """

    def __init__(
        self,
        base_url: str | None = None,
        org_id: str | None = None,
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("DEEPFLOW_API_BASE", "http://127.0.0.1:20416")
        ).rstrip("/")
        self.org_id = org_id or os.environ.get("DEEPFLOW_ORG_ID", "1")
        self.timeout = timeout
        self.session = session or requests.Session()

    def query(self, sql: str, db: str = "flow_log") -> list[dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/v1/query/",
            json={"db": db, "sql": sql},
            headers={"X-ORG-ID": str(self.org_id)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or payload.get("data") or {}
        columns = result.get("columns") or result.get("header") or []
        values = result.get("values") or result.get("data") or []
        if not columns:
            raise RuntimeError(f"unexpected DeepFlow response: {str(payload)[:500]}")
        columns = [item.get("name") if isinstance(item, dict) else item for item in columns]
        rows: list[dict[str, Any]] = []
        for value in values:
            if isinstance(value, dict):
                rows.append(value)
            else:
                rows.append(dict(zip(columns, value)))
        return rows

    def l7_flows(self, start: int, end: int) -> list[FlowRecord]:
        rows = self.query(L7_SQL_TEMPLATE.format(start=int(start), end=int(end)))
        records = []
        for row in rows:
            record = normalize_record(row)
            if record is not None:
                records.append(record)
        return records


def load_dataset(
    source: str = "replay",
    replay_dir: str | Path | None = None,
    deepflow_base: str | None = None,
    start: int | None = None,
    end: int | None = None,
) -> tuple[list[FlowRecord], list[NodeMetric], dict[str, Any] | None]:
    base = Path(replay_dir or REPLAY_DIR)
    if source == "replay":
        return (
            load_replay_flows(base / "l7_flow_log.csv"),
            load_node_metrics(base / "node_metrics.csv"),
            load_declared_topology(base / "declared_topology.json"),
        )
    if source == "deepflow":
        if start is None or end is None:
            raise ValueError("deepflow source requires a start/end window")
        client = DeepFlowClient(deepflow_base)
        try:
            node_rows = load_node_metrics(base / "node_metrics.csv")
        except FileNotFoundError:
            node_rows = []
        return (
            client.l7_flows(start, end),
            node_rows,
            load_declared_topology(base / "declared_topology.json"),
        )
    raise ValueError(f"unknown source: {source}")
