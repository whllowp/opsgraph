# OpsGraph · eBPF × UModel 运维图工程

> GitHub: [whllowp/opsgraph](https://github.com/whllowp/opsgraph)
>
> 把 eBPF 观测到的调用事实，编译成 UModel 上可查询、可推理、可回写的运维对象图。
> 不是再画一张拓扑图，而是把「图」当作运维系统的工程产物来设计、构建和演进。

![OpsGraph console](docs/console.png)

## 为什么需要 Graph Engineering

传统运维拓扑通常有三个断点：

1. **图是画出来的，不是算出来的。** CMDB 里的依赖靠人填，和真实流量长期漂移。
2. **边没有证据。** 一条 `A -> B` 的线，说不清 QPS、P99、错误率、起始时间，也说不清是声明还是观测。
3. **图不能推理。** 有拓扑但回答不了「为什么是它」「影响哪些业务」「摘掉它会怎样」。

OpsGraph 用 **eBPF 事实** 构建边，用 **UModel 语义层** 固化对象与关系，用 **图算子** 做因果排序、爆炸半径、影子依赖和变更对比。所有结论都能回到一条 UModel 查询和一组 eBPF 指标。

## 四层架构

```
                 ┌──────────────────────────────────────────────┐
                 │  DeepFlow agent (eBPF)                       │
                 │  L7 flow log · universal tags · node metrics │
                 └──────────────────────┬───────────────────────┘
                                        │  replay CSV / querier API
                                        ▼
┌─────────────────────┐      ┌──────────────────────┐      ┌─────────────────────┐
│ 1. Observe          │      │ 2. Model             │      │ 3. Materialize      │
│ DeepFlow adapter    │ ───► │ UModel model pack    │ ───► │ entities + relations │
│ normalize / window  │      │ 12 EntitySets/14 Link│      │ evidence on edges    │
└─────────────────────┘      └──────────────────────┘      └──────────┬──────────┘
                                                                     │
                                        ┌────────────────────────────▼──────────┐
                                        │ 4. Reason                             │
                                        │ RCA ranking · blast radius · drift    │
                                        │ SPOF · critical path · simulation     │
                                        └────────────────────────────┬──────────┘
                                                                     │
                     ┌───────────────────────────────────────────────┼──────────────────────────────┐
                     ▼                                               ▼                              ▼
              UModel Query Service                          Graph Console                  Markdown report
              `.entity` / `.topo` / `.umodel`               (SVG + evidence)               (incident-report.md)
```

## 5 分钟运行

### 前置条件

- Python 3.11+（依赖仅 `requests`）
- Go 1.22+（构建 UModel server）
- 同目录下的 [`UnifiedModel`](../UnifiedModel) 仓库
- Windows PowerShell 7+（下面的脚本；Linux/macOS 可用手动命令）

### 一键启动

```powershell
cd E:\ebpf\opsgraph
.\run-demo.ps1
```

脚本会：

1. 构建/启动 UModel（`file.memory`，`127.0.0.1:18080`）；
2. 生成 DeepFlow 形态的回放数据；
3. 导入 UModel 模型包，写入 86 个实体 / 151 条关系；
4. 运行图分析并把根因角色回写 UModel；
5. 启动控制台 `http://127.0.0.1:18088`。

停止：

```powershell
.\stop-demo.ps1
```

### 手动运行

```powershell
# 1. 启动 UModel（--import-root 必须覆盖本项目目录）
E:\ebpf\bin\umodel-server.exe --graphstore file.memory `
  --data .runtime\umodel --addr 127.0.0.1:18080 --import-root E:\ebpf\opsgraph

# 2. 生成回放 + 导入模型 + 写入实体关系 + RCA
python -m opsgraph demo --addr http://127.0.0.1:18080

# 3. 打开图控制台
python -m opsgraph serve --port 18088
```

## Demo 里会发生什么

场景：**一次 checkout retry 配置变更，在 Flash Sale 流量下放大成 payment-gateway 的 P99 SLO 故障。**

| 时间 | 事件 |
|---|---|
| 23:50 | `payment-gateway v3.2.1` 部署，只改日志格式（红鲱鱼） |
| 00:05 | Flash Sale 开始放量，3.2x |
| 00:10 | `checkout-service` 重试策略 `max_retries 2 -> 5`、超时 `500ms -> 2000ms` |
| 00:10 | checkout 出向流量开始放大（早于时延） |
| 00:13 | payment-gateway / payment-router P99 同步抬升 |
| 00:19 | Alipay 5xx 开始上升 |
| 00:20 | node-3 CPU 92%（batch-reporter 造成，不在支付路径） |
| 00:40 | SLO burn-rate 告警，进入事故 |

OpsGraph 的输出：

```text
Root cause: checkout-service  (confidence 78%)
Victim:     payment-gateway
Path:       checkout-service -> payment-gateway -> payment-router -> Alipay Gateway

#1 checkout-service  0.96   high-risk config @00:10 + outbound 4.6x + upstream of victim
#2 payment-gateway   0.47   victim, anomalous but no risky change
#3 payment-router    0.41   downstream, no change, anomaly fully explained by upstream
```

图工程发现：

- **retry-storm**：checkout → payment-gateway 达 3.3k rps，是基线的 8x，而 Flash Sale 只解释 3.2x；多出来的约 2.5x 来自重试放大。
- **shadow-dependency**：`checkout-service -> api.promo-flash.com` 只被 eBPF 观测到，CMDB 中没有声明。
- **declared-unobserved**：`order-service -> notification-service` 在 CMDB 中声明，但窗口内 0 请求。
- **single-point-of-failure**：服务图的割点，摘除会导致调用链断开。
- **node-pressure（已排除）**：node-3 CPU 92%，但 payment-gateway 没有 Pod 在该节点。

完整报告：`artifacts/incident-report.md`。

## 图模型：12 个实体集 / 14 条关系

模型定义在 `model/`，由 `opsgraph/ontology.py` 生成；导入后可在 UModel 里按 `.umodel` 查询。

| 域 | 实体集 | 关键字段 |
|---|---|---|
| infra | `infra.cluster` / `infra.node` / `infra.namespace` / `infra.workload` / `infra.pod` | CPU、AZ、Pod、镜像、副本 |
| svc | `svc.service` | QPS、P99、错误率、基线、异常分、onset、角色、变更风险 |
| svc | `svc.endpoint` | method/path、QPS、P99、错误率 |
| svc | `svc.external_service` | 域名、类型、声明状态、shadow 标记 |
| biz | `biz.journey` | 等级、转化影响、营收敞口、是否受影响 |
| ops | `ops.incident` / `ops.change` / `ops.alert` | 严重级别、时间、变更摘要、根因回写 |

关系：`contains`、`scheduled_on`、`runs_as`、`exposes`、`calls`、`calls_endpoint`、`depends_on`、`affects`、`targets`。

**边即证据。** `calls` 关系自带：

```json
{
  "rps": 3334.7,
  "error_rate": 0.0116,
  "latency_p99_ms": 2255.2,
  "baseline_p99_ms": 110.4,
  "p99_delta_pct": 1915.4,
  "onset_ts": 1793549580,
  "observed": true,
  "declared": true,
  "shadow": false,
  "evidence": "deepflow.l7_flow_log",
  "confidence": 0.98
}
```

## eBPF → UModel 映射

| DeepFlow 通用标签/指标 | OpsGraph 落点 |
|---|---|
| `pod_service_0` / `pod_service_1` | `svc.service`（客户端/服务端） |
| `pod_0` / `pod_1` | `infra.pod` |
| `pod_ns_0` / `pod_ns_1` | `infra.namespace` |
| `pod_node_0` / `pod_node_1` | `infra.node` |
| `request_domain` + `is_internet_1` | `svc.external_service`（含未声明的 shadow 依赖） |
| `endpoint` + `request_type` | `svc.endpoint` |
| `request` / `rrt_p99` / `client_error` / `server_error` | 关系上的 QPS / P99 / 错误率 |
| `tap_side='server'` | 去重，避免客户端与服务端双计 |

实时接入：

```powershell
$env:DEEPFLOW_API_BASE = "http://deepflow-server:20416"
python -m opsgraph demo --source deepflow
```

`opsgraph/deepflow.py` 中的 SQL 直接查询 `flow_log.l7_flow_log`，按分钟与通用标签聚合；详见 [DeepFlow 接入](docs/deepflow.md)。

## 图工程算子

| 算子 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `materialize` | L7 flow + k8s 快照 | 实体/关系批次 | 稳定 ID、证据字段、观测/声明对账 |
| `rca` | 受害服务 + 异常边 | 候选排序 + 置信度 | 自身严重度、变更关联、出向放大、因果领先、图位置 |
| `blast_radius` | 根因 | 服务/业务旅程/峰值 QPS | 沿 `calls` 正向可达 |
| `drift` | 观测边 vs 声明边 | shadow / unobserved | 双向差异，不静默丢边 |
| `spof` | 服务调用图 | 割点 | Tarjan |
| `critical_path` | 受害服务 | 高 P99 路径 | 用于定位最贵的一段 |
| `simulate` | 待摘除服务 | 受影响服务 | 控制台按钮，调 `/api/simulate` |

RCA 评分不是黑盒：

```text
score = 0.28 * self_severity
      + 0.52 * change_affinity      # 高风险变更 + 距放大起点 < 20min
      + 0.12 * outbound_amplification
      + 0.08 * causal_lead
      + 0.07 * propagation
      + 0.20 * graph_position       # 位于受害服务上游
```

控制台会展示每一项的分值；详见 [Graph Engineering](docs/graph-engineering.md)。

## 常用命令

```powershell
python -m opsgraph demo --addr http://127.0.0.1:18080   # 全链路
python -m opsgraph analyze --source umodel              # 从 UModel 重新读图分析
python -m opsgraph rca                                  # 打印根因排序
python -m opsgraph serve --port 18088                   # 控制台
python -m opsgraph doctor                               # UModel 连通性/计数
python -m opsgraph query ".entity with(domain='svc', name='svc.service') | limit 5"
python -m unittest discover -s tests -v                 # 24 个单测
```

## UModel 查询示例

```bash
# 受害服务
umctl --addr http://127.0.0.1:18080 query run opsgraph \
  ".entity with(domain='svc', name='svc.service', query='payment-gateway')"

# 读取 checkout 的直接调用边（含证据）
umctl --addr http://127.0.0.1:18080 query run opsgraph \
  '.topo | graph-call getDirectRelations([(:"svc@svc.service" {__entity_id__: "<checkout-id>"})])'

# 用 Cypher 拉完整证据图
umctl --addr http://127.0.0.1:18080 query run opsgraph \
  ".topo | graph-call cypher(\`MATCH (src)-[r]->(dest) RETURN src, r AS relation, dest LIMIT 100\`)"
```

## 目录

```
opsgraph/
├── opsgraph/
│   ├── scenario.py        # 确定性 DeepFlow 回放（含红鲱鱼/影子依赖）
│   ├── deepflow.py        # replay + live querier 适配
│   ├── ontology.py        # 图契约：12 EntitySet + 14 Link
│   ├── model_pack.py      # 生成 UModel 模型包
│   ├── materialize.py     # 观测 -> 实体/关系（证据边）
│   ├── analysis.py        # RCA / 影响面 / 漂移 / SPOF
│   ├── pipeline.py        # 全链路编排 + 报告
│   ├── console_server.py  # 控制台 API + 静态服务
│   ├── umodel_client.py   # UModel 公共 REST 客户端
│   └── cli.py
├── model/                 # 生成的 UModel 模型包
├── data/replay/           # l7_flow_log.csv / node_metrics.csv
├── console/               # 零依赖 SVG 图控制台
├── artifacts/             # snapshot / analysis / report
├── docs/
├── tests/
└── run-demo.ps1
```

## 验证

```powershell
python -m unittest discover -s tests -v
python -m opsgraph doctor
```

`verification.json` 会对比本地快照与 UModel 回读：

```json
{
  "declared_entities": 86,
  "umodel_entities": 86,
  "declared_relations": 151,
  "umodel_relations": 151,
  "root_cause_field": "checkout-service"
}
```

## 设计边界

- **DeepFlow 是可替换的传感器。** 只要能把 L7 调用聚合到「客户端服务、服务端服务/域名、端点、时间、QPS、P99、错误」这些维度，就可以替换 `deepflow.py` 的适配层。
- **UModel 是唯一读路径。** 控制台与 CLI 读取都通过 UModel Query Service；本地 `snapshot.json` 只是同一批写入数据的分析快照。
- **RCA 是可解释的。** 当前是规则 + 因果图排序，不是黑盒模型；每个分值都有对应证据字段。
- **不是生产级采集器。** 生产化需要处理多集群、采样、卡片性、权限、长期存储与告警闭环；这些在 [架构文档](docs/architecture.md) 的「下一步」中列出。

## License

Apache-2.0。示例中的服务名、IP、指标均为合成数据。
