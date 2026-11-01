# DeepFlow 接入

## 1. 数据面

OpsGraph 只需要 `flow_log.l7_flow_log` 的调用聚合；Materializer 会把返回的 `time` 行归并到分钟。默认 SQL（`opsgraph/deepflow.py`）：

```sql
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
```

要点：

- `tap_side = 'server'`：避免客户端和服务端各算一次。
- `time` 由 DeepFlow 按数据精度对齐；`Materializer` 以 `time // 60` 归并到分钟。
- 大窗口优先查 `1m` 聚合表，避免原始 `l7_flow_log` 扫描过大。
- `quantile(0.99)(rrt)` 在部分版本/表上不可用；如果不可用，退回 `Avg(rrt)`，`normalize_record` 会按均值 ×1.9 估算 P99。
- `request` 是窗口内请求数；`rps = request / 60`。
- 外部依赖通过 `request_domain` + `is_internet_1=1` 识别；`pod_service_1` 为空时 `normalize_record` 会标记 `external=True`。

## 2. 字段映射

| DeepFlow | OpsGraph |
|---|---|
| `pod_service_0` | 客户端 `svc.service` |
| `pod_service_1` | 服务端 `svc.service` |
| `pod_0` / `pod_1` | `infra.pod` |
| `pod_ns_0` / `pod_ns_1` | `infra.namespace` |
| `pod_node_0` / `pod_node_1` | `infra.node` |
| `request_domain` | `svc.external_service.domain` |
| `endpoint` | `svc.endpoint.path` |
| `request_type` | `svc.endpoint.method` |
| `l7_protocol_str` | `svc.endpoint.protocol` |
| `request` | `rps`、`requests_per_min` |
| `rrt_p99` | `latency_p99_ms` |
| `client_error` + `server_error` | `error_rate` |
| `is_internet_1` | `external` |

## 3. 实时模式

```powershell
$env:DEEPFLOW_API_BASE = "http://deepflow-server.deepflow.svc:20416"
$env:DEEPFLOW_ORG_ID = "1"

python -m opsgraph demo --source deepflow --addr http://127.0.0.1:18080
```

`DeepFlowClient.query()` 调用：

```http
POST {DEEPFLOW_API_BASE}/v1/query/
X-ORG-ID: {DEEPFLOW_ORG_ID}
Content-Type: application/json

{"db": "flow_log", "sql": "..."}
```

返回：

```json
{
  "result": {
    "columns": ["time", "pod_service_0", "..."],
    "values": [[1793548800, "checkout-service", "..."]]
  }
}
```

适配层同时兼容 `columns/values` 与 `header/data` 两种返回形状。

### 时间窗口

默认窗口是 `config.py` 中的 `SCENARIO_START` / `WINDOW_MINUTES`。实时接入可以显式传窗口：

```bash
python -m opsgraph demo --source deepflow \
  --start 1793548800 --end 1793552400
```

也可以用环境变量：

```bash
export OPSGRAPH_WINDOW_START=1793548800
export OPSGRAPH_WINDOW_MINUTES=60
python -m opsgraph demo --source deepflow
```

`--start` / `--end` 优先级最高；`--end` 省略时按 `WINDOW_MINUTES` 计算。回放场景仍使用固定的场景时间线，不受这两个变量影响。

## 4. 节点指标

回放数据包含 `node_metrics.csv`（CPU/内存/Pod 数）。实时模式默认仍读该 CSV；生产环境应接 Prometheus：

```promql
100 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100
```

把结果写成同样的 `NodeMetric`（`time, node, cpu, memory, pod_count`）即可，不需要改 Materializer。

## 5. 已知边界

- **NAT / Service VIP**：同一连接可能在多个 tap 点被观测；生产化需要按 `flow_id` 去重或只用 `tap_side='server'`。
- **加密流量**：只能看到 L7 元数据（域名、端点、状态码），无法解析业务字段；这既是隐私优势也是诊断边界。
- **采样**：DeepFlow 采样会低估 QPS，但比率类指标（错误率、P99）仍可用；RCA 的放大倍数建议用采样率校正。
- **多集群**：`pod_cluster` 需要进入实体 ID 或作为过滤维度，否则同名服务会合并。
- **长窗口**：`l7_flow_log` 原始表数据量大，生产查询应使用 `1m` 聚合表或 `flow_metrics`，并限制 `LIMIT`。
