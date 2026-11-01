# Demo 导览

## 场景

一次「重试配置变更 × 促销流量」叠加导致的支付故障。数据是合成的，但字段、时间线和因果关系按真实 eBPF 可观测性设计。

**期望答案**（写在 `data/replay/scenario.json`，pipeline 不读取）：

```json
{
  "expected_root_cause": "checkout-service",
  "expected_victim": "payment-gateway",
  "expected_findings": ["shadow-dependency", "declared-but-unobserved", "node-3 red herring"]
}
```

## 时间线

| 时间 | 事件 | 图上证据 |
|---|---|---|
| 23:50 | payment-gateway v3.2.1 部署（日志格式） | 低风险变更，距放大起点 20min |
| 00:05 | Flash Sale 开始 | 3.2x 流量 |
| 00:10 | checkout 重试策略变更 | 高风险 config，retry/timeout 关键词 |
| 00:10 | checkout 出向放大 | `outbound_amp=4.56`，早于时延 |
| 00:13 | payment-gateway P99 抬升 | P99 2.2s，基线 110ms |
| 00:13 | payment-router 跟随 | P99 1.96s |
| 00:19 | Alipay 5xx | 错误率 ~19% |
| 00:20 | node-3 CPU 92% | batch-reporter，不在支付路径 |
| 00:40 | SLO 告警 | burn rate 14.2x |
| 00:44 | SRE Agent 开始排查 | AgentSight 会话 `sre-claude-01` |
| 00:47 | Agent 写入 retry 配置 | 2 次 `apply_patch` 失败后成功 |
| 00:50 | Agent 应用 prod | 2 次 `kubectl_apply` forbidden 后成功 |
| 00:54 | Agent 验证 SLO | P99 仍 2.2s，remediation pending |

## Agent 图

右侧 `Agent` tab：

- 2 个 session：SRE Agent（high risk）与编码 Agent（low risk）
- 卡片上直接显示 tokens、成本、tool 数、失败数、重试环
- 点开 SRE session：`claim verified` + `remediation pending_validation`
- 下方 tool → effect 时间线：`apply_patch`/`kubectl_apply` 的失败与重试
- Agent 图上的紫色连线：`session --acts_on--> checkout-service`

AI 图的 UModel 查询：

```bash
umctl query run opsgraph \
  ".entity with(domain='ai', name='ai.agent_session') | sort risk_score desc"
```

## 运行后看什么

### 1. 控制台首屏

- 左栏：事故摘要、根因 `checkout-service`、78% 置信度、图契约计数。
- 中间：分层服务图（BUSINESS / EDGE / CORE / PAYMENT / CHANNEL / DATA），金色虚线为根因路径。
- 右栏：根因候选、图工程发现、已排除项。

### 2. 点击 `checkout-service`

- 详情卡展示 QPS、P99、错误率、出向放大、onset。
- 「模拟摘除」会高亮其下游影响。
- 「UModel 查询」自动切到查询页并执行对应 SPL。

### 3. 右栏「图工程发现」

- 影子依赖：`checkout-service -> api.promo-flash.com`，127 rps、P99 947ms、4% 错误，CMDB 未声明。
- 声明未观测：`order-service -> notification-service`，0 请求。
- 单点：服务图割点。
- 重试风暴：3.3k rps vs 3.2x 促销，差值的约 2.5x 来自重试。

### 4. 「时间线」

按分钟展示变更、信号、告警、事故；信号时间来自实际异常检测，不是硬编码。

## CLI 复现

```powershell
# 全链路
python -m opsgraph demo --addr http://127.0.0.1:18080

# 根因排序
python -m opsgraph rca

# UModel 查询
python -m opsgraph query ".entity with(domain='svc', name='svc.service', query='payment-gateway')"

# 从 UModel 回读整图后重新分析
python -m opsgraph analyze --source umodel
```

`rca` 输出示例：

```text
 1. checkout-service         score=0.959  critical
     - P99 1928ms, anomaly 17.1
     - high-risk config at 00:10 touching retry/timeout settings
     - outbound 4.6x baseline
     - upstream of victim (1 hop)
 2. payment-gateway          score=0.470  critical
     - P99 2228ms, anomaly 25.7
     - recent deployment is low risk
     - outbound 8.0x baseline
     - victim service
```

## 重置

```powershell
.\stop-demo.ps1
Remove-Item -Recurse -Force .runtime
Remove-Item -Recurse -Force artifacts
python -m opsgraph replay
python -m opsgraph model
```

UModel 使用 `file.memory` 时数据在 `.runtime/umodel`；删除即重置。若要保留图，改用 Neo4j/Gremlin provider。

## 截图

`docs/console.png` 是 1600×1000 的 headless Chrome 截图，由：

```powershell
chrome --headless=new --window-size=1600,1000 --screenshot=docs/console.png http://127.0.0.1:18088
```

生成。控制台不依赖外网字体或 CDN，离线可用。
