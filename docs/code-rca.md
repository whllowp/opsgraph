# 代码级 RCA / REA

## 1. 一句话

eBPF 不只知道「哪个服务慢」，还能通过用户态栈帧知道「哪一行代码慢、报错、阻塞」。OpsGraph 把这条证据链变成：

```text
eBPF code stack -> 代码行 RCA -> 伪代码方案 -> 补丁 -> 金融回归 -> eBPF 复测
```

**REA = Remediation Engineering Automation**：不是自动改生产，而是用可解释的 eBPF 证据驱动「定位 → 方案 → 补丁 → 测试 → 验证」的工程闭环。

## 2. eBPF 代码栈数据

当前 demo 使用可复现的代码栈回放，schema 与真实 uprobe/perf 输出对齐：

```json
{
  "id": "case-01-before-0001",
  "case_id": "case-01",
  "phase": "incident",
  "kind": "oncpu",
  "duration_us": 2800000,
  "error": true,
  "pid": 4201,
  "comm": "python",
  "probe": "uprobe:python:frame",
  "stack": [
    {"symbol": "server.handle_request", "file": "server.py", "line": 8},
    {"symbol": "payments.py:retry_charge", "file": "payments.py", "line": 20},
    {"symbol": "payments.py:retry_charge", "file": "payments.py", "line": 22}
  ]
}
```

真实采集可用：

```bash
# Linux: 99Hz 用户态栈采样，带 PID/符号
sudo perf record -F 99 -g --call-graph dwarf -p <pid> -- sleep 30
sudo perf script -i perf.data -F comm,pid,period,ip,sym,dso,srcline > perf-stacks.txt

# 或 bpftrace uprobe/uretprobe 聚合
sudo bpftrace -e 'profile:hz:99 /pid == <pid>/ { @[ustack] = count(); }'
```

本仓库已包含一份真实 `perf script` 样本 `data/code_rca/perf-sample.txt`：在 WSL Ubuntu 用 `perf` 采集一个 C 微服务，输出包含 `retry_charge` + `fintech_demo.c:5`。`ebpf_stacks.py` 会把 perf 的 leaf-first 栈反转成 root→leaf，并把它映射到：

```text
fintech_demo.c:5  retry_charge  total += amount * 0.01;
```

`code-ingest` 可复现：

```bash
python -m opsgraph code-ingest \
  --input data/code_rca/perf-sample.txt \
  --case case-01 \
  --repo demo/fintech
```

生产接入需要：

- 调试符号 / DWARF 或 Python frame 映射；
- 容器内路径到仓库路径的映射；
- 采样率、符号化、栈聚合；
- 对 PII/密钥做过滤。

## 3. 代码 RCA 算法

对每条 `file:line`：

| 证据 | 含义 |
|---|---|
| `self_ratio` | 该行独占 eBPF 采样时间的比例 |
| `latency_delta` | 事故窗口 p95 相对基线的放大 |
| `error_rate` | 该行栈上的错误采样比例 |
| `changed` | 是否命中最近变更集 |
| `offcpu_ratio` | 阻塞（off-CPU）占比 |

综合分：

```text
score = 0.50 * self_ratio
      + 0.20 * min(latency_delta, 3) / 3
      + 0.15 * error_rate
      + 0.10 * changed
      + 0.05 * offcpu_ratio
```

输出：

- `finding`：文件、行、函数、置信度、证据样本 ID、原因；
- `ranking`：Top N 代码行；
- `ruled_out`：高 CPU 但无错误/延迟变化的干扰项；
- `code_graph`：栈帧调用图（节点/边/计数）。

RCA 不读取测试名、补丁或预期答案；测试只用于验证定位是否正确。

## 4. REA 十次迭代

`opsgraph/code_rca.py` 在临时 worktree 中执行：

1. 读取当前故障的 eBPF 代码栈；
2. 代码行 RCA；
3. 生成伪代码（before/after）与补丁；
4. 应用补丁；
5. 跑该故障的金融回归；
6. 跑全量回归；
7. 用修复后 eBPF 采样复测 P99/错误率；
8. 通过则保留，失败则回滚；
9. 进入下一个故障；
10. 最后输出累计 diff、报告和小白解释。

Demo 的 10 个故障：

| # | 故障 | 代码 |
|---:|---|---|
| 1 | 重试导致重复扣款 | `payments.retry_charge` |
| 2 | 换汇差 1 分钱 | `money.convert_currency` |
| 3 | 费率档位边界错算 | `money.calculate_fee` |
| 4 | 分账多出 1 分钱 | `money.allocate_amount` |
| 5 | 退款回调重复入账 | `payments.refund` |
| 6 | 批量结算漏最后一页 | `ledger.settle_batch` |
| 7 | 账本部分写入 | `ledger.append_entries` |
| 8 | 欧元出款走错通道 | `routing.route_payout` |
| 9 | 风控超时拖垮支付 | `risk.fraud_score` |
| 10 | 结算日落在周末 | `scheduling.next_settlement_date` |

结果：

```text
10/10 fixed
10/10 RCA matches
11 candidates / 1 rejected by financial tests
最终 10 个金融回归全部通过
```

### 候选补丁与安全校验

`opsgraph/patch_engine.py` 不直接信任候选补丁：

- `old` 上下文必须精确匹配 1 次；
- 不允许修改测试文件；
- diff 行数有上限；
- 失败候选立即回滚，再尝试下一个；
- 通过回归后还要通过 eBPF P99/错误率复测。

Demo 中 `case-03` 先尝试「只修 1000 边界」的部分补丁，回归拒绝后由完整补丁接管；这次拒绝会记录在 `candidates` 里，而不是被静默忽略。

## 5. 小白用户视图

控制台「代码」tab 把每个故障翻译成四句话：

- **发生了什么**：客户被重复扣款 / 换汇少 1 分 / 结算漏单；
- **为什么**：重试没有幂等键 / 浮点精度 / 边界用错 / 分页提前退出；
- **怎么修**：伪代码 before/after；
- **怎么验证**：测试通过 + eBPF P99/错误率回落。

不需要读懂调用栈，也能理解「哪一行、为什么、改什么、怎么确认修好」。

## 6. 命令与产物

```bash
python -m opsgraph code-demo
python -m opsgraph code-demo --iterations 10
```

产物：

- `data/code_rca/ebpf_samples.json`：eBPF 代码栈回放；
- `artifacts/code_rca.json`：10 次迭代、RCA、伪代码、测试、复测；
- `artifacts/code_graph.json`：47 个代码实体 / 76 条代码关系；
- `artifacts/code-rca-report.md`：人类可读报告；
- `artifacts/code-fixes.patch`：累计补丁。

## 6.1 UModel 代码图

`code-demo` 会把代码域写入 UModel：

```text
code.repository --contains--> code.file --contains--> code.function
code.function --has_line--> code.line
code.line --observed_in--> code.issue
code.patch --fixes--> code.issue
code.patch --modifies--> code.file
code.issue --affects--> code.function
code.issue --affects--> svc.service
```

规模：`22 EntitySet / 29 Link`，`47` 个代码实体 / `76` 条关系。  
控制台「代码」tab 会显示 `UModel code graph · 47 entities / 76 relations`，查询 tab 可直接查 `code.issue`。

## 6.2 真实 eBPF 栈接入

```bash
# Linux: 99Hz 用户态栈 + DWARF
sudo perf record -F 99 -g --call-graph dwarf -p <pid> -- sleep 30
sudo perf script > perf-stacks.txt

# 导入并做代码行 RCA
python -m opsgraph code-ingest --input perf-stacks.txt --case case-01
```

也支持 folded stacks：

```bash
python -m opsgraph code-ingest --input stacks.folded --format folded --case case-01
```

`code-ingest` 只做 RCA，不自动改代码；它输出根因行、置信度、Top 3 代码行，供 REA 或人工继续。

控制台：`http://127.0.0.1:18088/?tab=code`

## 7. 边界

- 当前 demo 的 eBPF 栈是**可复现回放**，不是在本机真实内核采集；真实采集需要 Linux + perf/bpftrace/DWARF。
- 补丁来自人工审核的案例 fixture，不是 LLM 自动生成；REA 的重点是证据链、验证和回滚，而不是「让模型随便改代码」。
- 只在临时 worktree 改代码，不自动提交生产；高风险资金改动仍应经过门禁与人工审批。
- 行号依赖源码版本；生产需要 build-id/commit 与符号服务。
- Python 之外的运行时需要各自的栈映射（Go/JVM/Node 的符号化路径不同）。
