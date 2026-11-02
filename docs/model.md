# 模型

模型包由 `opsgraph/ontology.py` 生成，目录结构：

```
model/
├── entity_set/
│   ├── infra.cluster.json
│   ├── infra.node.json
│   ├── ...
│   └── ops.alert.json
├── link/
│   ├── svc.service_calls_svc.service.json
│   └── ...
└── manifest.json
```

## 实体集

| 名称 | 主键 | 用途 |
|---|---|---|
| `infra.cluster` | `id` | 集群边界与版本 |
| `infra.node` | `id` | 节点 CPU/内存/异常分值/onset |
| `infra.namespace` | `id` | 团队与命名空间 |
| `infra.workload` | `id` | Deployment/CronJob、镜像、副本 |
| `infra.pod` | `id` | Pod、节点、IP、重启次数 |
| `svc.service` | `id` | 服务黄金指标、基线、异常分、onset、RCA 角色、最近变更 |
| `svc.endpoint` | `id` | API 方法/路径、QPS、P99、错误率 |
| `svc.external_service` | `id` | 外部域名/DB/中间件，`declared`、`shadow` |
| `biz.journey` | `id` | 业务旅程、转化影响、营收敞口 |
| `ops.incident` | `id` | 事故、受害服务、根因回写 |
| `ops.change` | `id` | 部署/配置变更、风险、回滚 |
| `ops.alert` | `id` | 告警目标、信号、触发时间 |
| `ai.agent_session` | `id` | Agent 会话、tokens、成本、工具/失败/重试环、风险、claim/remediation 校验 |
| `ai.model` | `id` | LLM provider/model、调用数、tokens、成本 |
| `ai.tool_call` | `id` | 工具决策、输入/输出摘要、目标、状态、重试序号、风险 |
| `ai.effect` | `id` | file/process/network/llm 效果、目标、风险、置信度 |
| `code.repository` | `id` | 代码仓库、分支、commit、语言 |
| `code.file` | `id` | 源码文件、行数、变更状态 |
| `code.function` | `id` | 函数限定名、起止行 |
| `code.line` | `id` | eBPF 归因行、独占时间、P95、错误率、RCA 分数 |
| `code.issue` | `id` | 代码级金融故障、SLO、修复前后指标 |
| `code.patch` | `id` | 伪代码策略、diff、回归、eBPF 复测 |

所有实体都有：

```json
{
  "__domain__": "svc",
  "__entity_type__": "svc.service",
  "__entity_id__": "32-char-md5",
  "__category__": "entity",
  "__method__": "Update",
  "__first_observed_time__": 1793548800,
  "__last_observed_time__": 1793552400,
  "__keep_alive_seconds__": 600,
  "id": "same-as-entity-id",
  "display_name": "checkout-service"
}
```

## 关系

| 名称 | src → dest | 语义 |
|---|---|---|
| `infra.cluster_contains_infra.node` | cluster → node | 集群包含节点 |
| `infra.namespace_contains_infra.workload` | namespace → workload | 命名空间包含工作负载 |
| `infra.workload_contains_infra.pod` | workload → pod | 工作负载包含 Pod |
| `infra.pod_scheduled_on_infra.node` | pod → node | Pod 调度到节点 |
| `svc.service_runs_as_infra.workload` | service → workload | 服务由工作负载运行 |
| `svc.service_exposes_svc.endpoint` | service → endpoint | 服务暴露端点 |
| `svc.service_calls_svc.service` | service → service | eBPF 观测的服务调用 |
| `svc.service_calls_svc.external_service` | service → external | eBPF 观测的外部依赖 |
| `svc.service_calls_endpoint_svc.endpoint` | service → endpoint | 端点级调用证据 |
| `biz.journey_depends_on_svc.service` | journey → service | 业务依赖 |
| `ops.incident_affects_svc.service` | incident → service | 事故影响服务 |
| `ops.change_targets_svc.service` | change → service | 变更作用于服务 |
| `ops.alert_targets_svc.service` | alert → service | 告警指向服务 |
| `ops.alert_targets_infra.node` | alert → node | 告警指向节点 |
| `ai.agent_session_uses_model_ai.model` | agent_session → model | 会话使用模型 |
| `ai.agent_session_invokes_ai.tool_call` | agent_session → tool_call | 会话发起工具调用 |
| `ai.tool_call_produces_ai.effect` | tool_call → effect | 工具调用产生系统效果 |
| `ai.agent_session_acts_on_svc.service` | agent_session → service | Agent 作用于服务 |
| `ai.agent_session_investigates_ops.incident` | agent_session → incident | Agent 排查事件 |
| `ai.effect_affects_svc.service` | effect → service | 系统效果影响服务 |
| `ai.agent_session_delegates_to_ai.agent_session` | agent_session → agent_session | Agent 委派子会话 |
| `code.repository_contains_code.file` | repository → file | 仓库包含文件 |
| `code.file_contains_code.function` | file → function | 文件包含函数 |
| `code.function_has_code.line` | function → line | 函数包含采样行 |
| `code.line_observed_in_code.issue` | line → issue | 代码行出现于故障 |
| `code.issue_affects_code.function` | issue → function | 故障根因函数 |
| `code.patch_fixes_code.issue` | patch → issue | 补丁修复故障 |
| `code.patch_modifies_code.file` | patch → file | 补丁修改文件 |
| `code.issue_affects_svc.service` | issue → service | 代码故障影响服务 |

### `calls` 关系字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `rps` | float | 窗口末分钟请求速率 |
| `error_rate` | float | (client_error + server_error) / request |
| `latency_p99_ms` | float | 请求级 P99（DeepFlow `quantile(0.99)(rrt)`） |
| `baseline_p99_ms` | float | 前 10 分钟 P99 中位数 |
| `p99_delta_pct` | float | 相对基线变化百分比 |
| `onset_ts` | integer | 时延/错误异常起始（unix 秒） |
| `observed` | boolean | 窗口内是否有流量 |
| `declared` | boolean | 是否在声明拓扑中 |
| `shadow` | boolean | 观测到但未声明 |
| `evidence` | string | `deepflow.l7_flow_log` 或 `declared_topology` |
| `confidence` | float | 观测置信度 |
| `requests_per_min` | integer | requests / 分钟 |
| `bytes_per_min` | integer | 双向字节数 / 分钟 |

### AI 关系字段

| 关系 | 字段 |
|---|---|
| `uses_model` | `tokens`, `cost_usd` |
| `invokes` | `status`, `risk`, `retry_index` |
| `produces` | `risk`, `status` |
| `acts_on` | 无（语义边） |
| `investigates` | 无（语义边） |
| `delegates_to` | `role`, `tokens` |
| `affects` | `risk` |

AI 实体核心字段：

- `ai.agent_session`: `session_key`, `model`, `total_tokens`, `cost_usd`, `tool_calls`, `failed_tool_calls`, `retry_loops`, `risk_score`, `friction_score`, `claim_status`, `remediation_status`, `parent_session`, `root_session`, `depth`, `delegation_role`, `child_sessions`, `subtree_tokens`, `subtree_cost_usd`, `subtree_risk_score`
- `ai.tool_call`: `tool_name`, `status`, `target`, `command`, `retry_index`, `retry_of`, `risk`
- `ai.effect`: `effect_type`, `action`, `target`, `status`, `risk`, `confidence`

代码域核心字段：

- `code.line`: `file`, `line`, `function`, `source`, `self_ms`, `p95_ms`, `error_rate`, `changed`, `score`, `confidence`
- `code.issue`: `case_id`, `severity`, `symptom`, `status`, `slo_p99_ms`, `p99_before_ms`, `p99_after_ms`, `error_before`, `error_after`
- `code.patch`: `case_id`, `file`, `function`, `strategy`, `diff`, `test`, `verification`, `status`

## ID 规则

```text
entity_id = md5("{domain}/{entity_type}/{natural_key}")[:32]
```

- service：`svc/svc.service/checkout-service`
- external：`svc/svc.external_service/api.promo-flash.com`
- endpoint：`svc/svc.endpoint/checkout-service:POST /api/v1/checkout`
- pod：`infra/infra.pod/checkout-service-623e1-0`
- agent session：`ai/ai.agent_session/sre-claude-01`
- tool call：`ai/ai.tool_call/tool-sre-apply-1`
- effect：`ai/ai.effect/audit-sre-file-write`

自然键稳定，所以重放、重跑、跨环境迁移都不会产生重复实体。

## 导入顺序

UModel 对关系做前向引用校验，因此 pipeline 分两步：

```python
client.import_model("model/entity_set")
client.import_model("model/link")
```

然后：

```python
client.write_entities(entities)   # 400/批
client.write_relations(relations) # 400/批
```

## 查询示例

```bash
# 列出实体集
umctl query run opsgraph ".umodel with(kind='entity_set') | sort name | limit 20"

# 查看某个服务的全部字段
umctl query run opsgraph ".entity with(domain='svc', name='svc.service', query='checkout-service')"

# 查看 checkout 的直接关系
umctl query run opsgraph \
  '.topo | graph-call getDirectRelations([(:"svc@svc.service" {__entity_id__: "<id>"})])'

# 用 Cypher 拉全图（分页，避免 100 行上限）
umctl query run opsgraph \
  ".topo | graph-call cypher(\`MATCH (src)-[r]->(dest) RETURN src, r AS relation, dest SKIP 0 LIMIT 100\`)"

# Agent 会话与风险
umctl query run opsgraph \
  ".entity with(domain='ai', name='ai.agent_session') | sort risk_score desc | project display_name,total_tokens,cost_usd,risk_level,claim_status"

# 代码级 RCA 故障
umctl query run opsgraph \
  ".entity with(domain='code', name='code.issue') | sort p99_before_ms desc | project display_name,case_id,status,p99_before_ms,p99_after_ms"
```
