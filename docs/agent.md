# Agent 接入

OpsGraph 不绑定某个 Agent 框架；它把「图」暴露成 UModel 的公共查询面。Agent 有两种接入方式：

- **CLI / REST（推荐）**：直接查正在运行的 UModel server，最稳定。
- **MCP**：`umodel-mcp` 是内嵌 UModel 的 stdio server，不是远程代理；需要让它读取同一份 `file.memory` 数据目录。

## 1. MCP 配置

`umodel-mcp` 的 `--addr` 是 MCP HTTP 监听地址，不是 UModel server 地址。正确配置：

```json
{
  "mcpServers": {
    "umodel": {
      "command": "go",
      "args": [
        "run", "./cmd/umodel-mcp",
        "--data", "E:/ebpf/opsgraph/.runtime/umodel",
        "--graphstore", "file.memory",
        "--workspace", "opsgraph"
      ],
      "cwd": "E:/ebpf/UnifiedModel"
    }
  }
}
```

注意：MCP 进程会打开同一个 `file.memory` 数据目录。不要让 MCP 与控制台 server 同时写同一 workspace；查询只读时问题不大，生产环境应改用 Neo4j/Gremlin 或独立的只读副本。

如果只是想验证 MCP 工具面，最省事的是用 UModel 自带的 quickstart：

```bash
go run ./cmd/umodel-mcp --quickstart --quickstart-sample examples/incident-investigation
```

## 2. 推荐提示词

> `payment-gateway` 的 P99 SLO 告警了。请通过 UModel 对象图排查：
> 1. 找到受害服务和它的上游调用者；
> 2. 检查上游最近的变更；
> 3. 用 `calls` 边上的 `rps`、`latency_p99_ms`、`p99_delta_pct`、`onset_ts` 说明因果顺序；
> 4. 给出根因、影响面和一条可回滚的动作；
> 5. 把结论写回 `ops.incident.root_cause`。

Agent 可以先跑：

```bash
python -m opsgraph rca
```

拿到结构化排序，再用 UModel 查询验证每条证据。

## 3. 关键查询

```bash
# 受害服务
umctl query run opsgraph ".entity with(domain='svc', name='svc.service', query='payment-gateway')"

# 上游调用者
umctl query run opsgraph \
  '.topo | graph-call getNeighborNodes("upstream", 2, [(:"svc@svc.service" {__entity_id__: "<victim-id>"})])'

# 上游最近变更
umctl query run opsgraph ".entity with(domain='ops', name='ops.change', query='checkout') | sort applied_ts desc | limit 5"

# 业务影响
umctl query run opsgraph ".entity with(domain='biz', name='biz.journey', query='impacted')"
```

## 4. 给 Agent 的约束

- 只通过 UModel Query Service 读图，不直接读 `.runtime` 下的存储文件。
- 任何「根因」结论必须引用至少一条 `calls` 边和一个 `ops.change`（或明确说明没有变更）。
- 把「影子依赖」「声明未观测」作为发现项，不要静默忽略。
- 行动建议必须带风险等级、是否需人工确认、回滚方式。
- 结论回写 `ops.incident.root_cause` 与 `svc.service.role`，让下一次查询复用。

## 5. 可加载 Skill（示例）

`skills/opsgraph-rca/SKILL.md` 提供了一个最小 Skill 骨架，可复制到 `.claude/skills/`、`.qoder/skills/` 或 `.agents/skills/`。

它只做三件事：

1. 读取事故与受害服务；
2. 沿 `calls` 向上游遍历，收集变更与边证据；
3. 调用 `opsgraph rca` 对照排序，输出可解释结论。

## 6. 为什么不把 RCA 全交给 LLM

LLM 擅长叙述与假设生成，不擅长稳定地做图遍历和数值排序。OpsGraph 把「图计算」留在确定性代码里，把「解释、取舍、行动」留给 Agent。两者通过 UModel 的公共查询面解耦。
