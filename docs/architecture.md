# 架构

## 1. 设计目标

OpsGraph 把「运维对象图」拆成五个可独立验证的层：

| 层 | 职责 | 代码 |
|---|---|---|
| Observe | 从 eBPF 遥测得到 L7 调用与 Agent 行为 | `deepflow.py`, `scenario.py`, `agentsight.py`, `agentsight_demo.py` |
| Contract | 用 UModel 固化对象、关系与字段语义 | `ontology.py`, `model_pack.py` |
| Materialize | 稳定 ID、证据边、声明/观测对账、Agent 意图→效果链 | `materialize.py`, `agent_graph.py` |
| Reason | RCA、影响面、漂移、割点、模拟、Agent 风险/重试环/结论校验 | `analysis.py`, `agent_graph.py` |
| Act | 控制台、报告、UModel 查询、Agent | `console_server.py`, `pipeline.py` |

关键不变式：

- 所有运行时读取都走 UModel Query Service（`.entity` / `.topo` / `.umodel`），不直读 GraphStore。
- 实体 ID 由语义自然键的 MD5 生成，重跑幂等。
- 每条 `calls` 边必须携带证据字段；没有证据的边只能是 `observed=false` 的声明边。
- 观测到但未声明的依赖不丢弃，落成 `shadow=true` 的外部实体/边。
- 声明了但未观测到的依赖不丢弃，落成 `observed=false` 的边。
- Agent 的 `claim` 只是声明，不是事实；必须通过 `claim_status` 与图 RCA 对比。
- Agent 的 `remediation` 只有被 eBPF 指标验证后才能从 `pending_validation` 变为 `applied`。
- AgentSight 原始内容（prompt/response/命令全文）不写入图，只写摘要与派生指标。

## 2. 运行时数据流

```
DeepFlow l7_flow_log                 AgentSight snapshot / SQLite
  │  (pod_service, request_domain)     │  (sessions, tool_calls, audit_events)
  ▼                                    ▼
normalize_record()                 load_snapshot()/load_sqlite()
  │  FlowRecord                        │  AgentSnapshot
  ▼                                    ▼
Materializer                       AgentGraphBuilder
  │  entities + relations             │  ai.* entities + relations
  └──────────────┬────────────────────┘
                 ▼
        UModel REST (entities:write / relations:write)
                 │
                 ▼
        analyze(snapshot) + analyze_agents()
                 │
                 ├──► UModel 回写 role / root_cause / claim
                 ▼
        artifacts/{snapshot,analysis,agents,verification}.json
        artifacts/incident-report.md
        console (/api/state)
```

## 3. 组件边界

### 3.1 DeepFlow 适配层

只依赖 DeepFlow 的公共查询接口 `POST /v1/query/`。输入是 `l7_flow_log` 按 `time` 的聚合，Materializer 再归并到分钟；字段全部使用 DeepFlow 通用标签。回放模式读取相同列名的 CSV，因此从回放到实时只改数据源，不改图构建逻辑。

### 3.2 模型生成器

`ontology.py` 是唯一真源；`model_pack.py` 生成 `model/entity_set/*.json` 与 `model/link/*.json`。模型按「实体集先行、关系后行」分两次导入，避免 UModel 校验时前向引用失败。

### 3.3 Materializer

Materializer 不读 `scenario.expected_root_cause`，只读：

- `FlowRecord[]`：观测事实；
- `NodeMetric[]`：节点资源；
- `declared_topology`：K8s/CMDB 的声明对象与边。

因此换一份真实 DeepFlow 数据，它仍然工作。

### 3.4 分析引擎

分析在内存图上运行，输入是 Materializer 的 snapshot（与写入 UModel 的是同一批 payload）。`--source umodel` 可以改为从 UModel 回读整张图，验证「图上推理」与「写入口径」一致。

### 3.5 控制台

零前端依赖：原生 SVG + Vanilla JS。`/api/state` 返回布局后的节点/边，`/api/query` 代理 UModel SPL，`/api/analyze` 重跑分析，`/api/simulate` 做摘除模拟。

## 4. 时间与新鲜度

- 场景窗口固定 60 分钟，便于确定性回放与截图。
- 实体 `__keep_alive_seconds__ = 600`；关系继承首末观测时间。
- `onset_ts` 是异常起始分钟；`latency_onset_ts` 与 `amplification_onset_ts` 分开记录，避免把「流量放大」误判为「时延劣化」。
- 生产化应使用 `__last_observed_time__` 做 TTL 清理，并对「声明边长时间未观测」生成治理工单，而不是直接删除。

## 5. 失败模式与处理

| 失败 | 处理 |
|---|---|
| UModel 未启动 | pipeline 启动时 `wait_ready()`，失败直接退出并打印地址 |
| 模型导入已存在 | 重复导入返回 skipped，不视为错误 |
| 实体写入部分失败 | `partial_success=true`，统计 accepted/failed，`verification` 对账 |
| 某条边没有 baseline | 用序列首值做 baseline，并标记为 shadow/novel |
| 服务不在声明拓扑中 | 以观测到的服务名创建 `observed_only` 元数据，不丢数据 |
| 外部域名无法映射 | 用完整域名作为稳定 key，标记 `declared=false` |
| Cypher 结果上限 100 行 | `all_relations()` 使用 `SKIP/LIMIT` 分页 |

## 6. 生产化下一步

1. **采集层**：多 DeepFlow Org / 多集群；按 `pod_cluster` 分区；处理 NAT、Service VIP、sidecar 双计。
2. **图存储**：当前用 UModel `file.memory`；生产切 Neo4j 或 Gremlin，保留同一套公共查询。
3. **实时化**：把 Materializer 做成流式窗口（1m/5m），边证据按窗口增量更新。
4. **权限**：按 namespace/team 做行级过滤；控制台的 `/api/query` 需要鉴权与查询白名单。
5. **行动闭环**：把 `ops.change` 的 rollback 与 RunbookSet 工具接起来，RCA 结论可一键生成变更回滚草案。
6. **评测**：用历史事故回放做 RCA top-1/top-3 命中率、误报率、平均定位时间（MTTD）评估。
