# Graph Engineering：把运维图当作工程产物

## 1. 定义

**图工程（Graph Engineering）** 是对「对象、关系、证据、推理」进行契约化设计、可持续构建和可验证计算的实践。

它区别于三件事：

| 做法 | 产出 | 局限 |
|---|---|---|
| 监控大盘 | 指标曲线 | 看不到调用关系与传播路径 |
| CMDB 拓扑 | 人工维护的静态图 | 与真实流量漂移，边没有证据 |
| 单次 RCA 脚本 | 一次结论 | 不可复用、不可解释、不可回写 |

图工程要求图具备四个属性：

1. **有契约**：对象类型、关系语义、字段含义在模型层固定（UModel）。
2. **有证据**：每条边能回答「多少流量、多慢、多少错误、从什么时候开始、是观测还是声明」。
3. **有算子**：遍历、排序、影响面、割点、模拟都是图上的可复用计算。
4. **有闭环**：结论回写到图（`role`、`root_cause`、`impacted`），下一次查询/Agent 能直接复用。

## 2. 图契约

`opsgraph/ontology.py` 定义 12 个 EntitySet 与 14 条 Link。约束：

- 一个服务必须能通过 `runs_as` 落到 workload，再通过 `contains`/`scheduled_on` 落到 pod/node。
- 一条调用必须用 `calls`，端点级证据用 `calls_endpoint`。
- 业务影响必须通过 `depends_on` 从 journey 连到 service，而不是把业务字段塞进服务。
- 事件、变更、告警分别用 `affects` / `targets` 连接到对象。

契约带来的直接好处：RCA 不需要知道数据来自 DeepFlow 还是别的传感器，只需要图上有 `svc.service`、`calls`、`ops.change`。

## 3. 证据边（Evidence-carrying Edge）

普通拓扑边只有 `src -> dst`；OpsGraph 的边是一个观测记录：

```json
{
  "__relation_type__": "calls",
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

这使图查询可以直接回答「哪条边在 00:13 之后 P99 超过 1s 且错误率大于 1%」，而不需要回到指标系统做二次 join。

## 4. 观测与声明的对账

| 观测 | 声明 | 语义 | 处理 |
|---|---|---|---|
| 有 | 有 | 正常依赖 | `observed=true, declared=true` |
| 有 | 无 | **影子依赖** | 创建 `svc.external_service`，`shadow=true`，告警 |
| 无 | 有 | **僵尸依赖 / 集成断裂** | 保留边，`observed=false`，进入治理清单 |
| 无 | 无 | 不存在 | 不建边 |

这一步是图工程和「画拓扑」的分水岭：差异本身是运维信号，不是噪声。

## 5. RCA 排序

### 5.1 候选

候选 = 窗口内 `anomaly_score >= 3` 或 `status in {warning, critical}` 的服务，加上事故受害服务。

### 5.2 分量

| 分量 | 权重 | 计算 | 直觉 |
|---|---:|---|---|
| `self_severity` | 0.28 | P99/错误 z-score × 绝对严重度权重 | 服务自身有多糟 |
| `change_affinity` | 0.52 | 高风险变更 + 距放大起点 ≤20min | 变更是否解释异常 |
| `outbound_amplification` | 0.12 | `outbound_qps / baseline` | 重试风暴的典型指纹 |
| `causal_lead` | 0.08 | 候选起点早于受害服务多少分钟 | 因果必须先于症状 |
| `propagation` | 0.07 | 下游异常服务占比 | 能否解释大部分异常 |
| `graph_position` | 0.20 | 受害服务上游（1 跳 0.20，2 跳 0.18…） | 根因通常在受害者的上游 |

`self_severity` 使用绝对严重度加权，避免「12ms → 70ms」的 z-score 比「1.1s → 2.2s」还高。

### 5.3 置信度

```text
confidence = clamp(0.55 + 0.45 * (top1 - top2) / top1, 0, 0.98)
```

差距越大，置信度越高；控制台显示为百分比。它是「排序区分度」而不是概率，不能直接当概率使用。

### 5.4 为什么不是纯 AI

- 运维需要可解释：每个分量都能回到一条边或一个变更。
- 事故样本少：规则 + 图结构在冷启动时比监督模型稳。
- 可与 AI 组合：把排序结果、证据、runbook 交给 LLM 做归因叙述和行动建议，而不是让 LLM 从零猜。

## 6. 图算子

### 6.1 爆炸半径

从根因沿 `calls` 正向 BFS，得到受影响服务集合；再沿 `depends_on` 找到业务旅程，汇总转化影响与营收敞口。

### 6.2 单点

对服务调用图做无向化 Tarjan，输出割点。割点意味着摘除后图分裂，是结构性风险。

### 6.3 关键路径

从受害服务出发，选 P99 最高的外部依赖路径，用于定位「最贵的一段」。示例：payment-gateway -> payment-router -> Alipay Gateway。

### 6.4 摘除模拟

删除服务节点后重新计算下游可达集合。控制台只做只读模拟，不修改 UModel；生产化可以生成变更预案。

## 7. 边界

- 当前只对服务级做 RCA；数据库/中间件级根因需要把外部依赖纳入候选。
- 重试放大是「出向 QPS / 基线」的启发式，遇到批量任务或缓存穿透需要白名单。
- 观测边来自 eBPF，加密流量只能看到 L7 元数据（域名、端点、状态码），看不到 payload；这是隐私优势也是诊断边界。
- 时间窗口固定 60 分钟，跨窗口的慢变更（例如 24h 前）需要扩大 `change_affinity` 的 lookback。
