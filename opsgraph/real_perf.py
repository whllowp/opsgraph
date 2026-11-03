"""Real ``perf`` capture -> C code RCA -> REA loop.

This is the Linux-only companion to the deterministic Python demo. It builds a
small C financial service with ten bugs, captures real user-space stacks with
``perf record -g --call-graph dwarf``, maps them to ``fintech.c:line`` with
``perf script -F ... srcline``, applies one patch per case, rebuilds, and runs
the C regression.

Requires Linux with ``perf`` (or ``PERF`` pointing at a perf binary) and gcc.
On Windows the module returns a clear ``skipped`` result instead of pretending
to have captured kernel samples.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .code_rca import analyze_code
from .ebpf_stacks import load_stacks
from .patch_engine import PatchCandidate, apply_candidate, restore_candidate
from .util import write_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
C_ROOT = PROJECT_ROOT / "demo" / "fintech_c"

CASES: list[dict[str, Any]] = [
    {
        "id": "c01",
        "index": 1,
        "title": "换汇截断分位",
        "file": "fintech.c",
        "function": "convert_currency",
        "strategy": "加半个分母后做整数四舍五入",
        "old": "    return (amount_cents * rate_num) / rate_den;",
        "new": "    return (amount_cents * rate_num + rate_den / 2) / rate_den;",
        "novice": {
            "what": "换汇金额少 1 分",
            "why": "整数除法把 50.5 分截成 50 分",
            "fix": "先加 rate_den/2 再整除",
        },
    },
    {
        "id": "c02",
        "index": 2,
        "title": "费率边界错算",
        "file": "fintech.c",
        "function": "calculate_fee",
        "strategy": "档位边界改为包含等号",
        "old": """    if (amount_cents > 100000) {
        return amount_cents * 5 / 1000;
    }
    if (amount_cents > 10000) {
        return amount_cents * 10 / 1000;
    }""",
        "new": """    if (amount_cents >= 100000) {
        return amount_cents * 5 / 1000;
    }
    if (amount_cents >= 10000) {
        return amount_cents * 10 / 1000;
    }""",
        "novice": {
            "what": "刚好 100 元的交易被多收手续费",
            "why": "边界使用 > 而不是 >=",
            "fix": "两个档位边界改为包含等号",
        },
    },
    {
        "id": "c03",
        "index": 3,
        "title": "分账丢失余数",
        "file": "fintech.c",
        "function": "allocate_amount",
        "strategy": "先分整数份，再按余数给前几份加 1 分",
        "old": """    for (int i = 0; i < parts; i++) {
        out[i] = total_cents / parts;
    }""",
        "new": """    long base = total_cents / parts;
    long remainder = total_cents % parts;
    for (int i = 0; i < parts; i++) {
        out[i] = base + (i < remainder ? 1 : 0);
    }""",
        "novice": {
            "what": "分账后总额少 1 分",
            "why": "整除把余数丢掉了",
            "fix": "余数依次分给前几份",
        },
    },
    {
        "id": "c04",
        "index": 4,
        "title": "重试重复扣款",
        "file": "fintech.c",
        "function": "retry_charge",
        "strategy": "扣款计数只允许从 0 变成 1",
        "old": "    *charges += 2;",
        "new": "    if (*charges == 0) {\n        *charges = 1;\n    }",
        "novice": {
            "what": "重试把客户扣了两次",
            "why": "每次重试都无条件加 2",
            "fix": "只在第一次扣款时置 1",
        },
    },
    {
        "id": "c05",
        "index": 5,
        "title": "退款重复入账",
        "file": "fintech.c",
        "function": "refund",
        "strategy": "退款计数只允许从 0 变成 1",
        "old": "    *refunds += 1;",
        "new": "    if (*refunds == 0) {\n        *refunds = 1;\n    }",
        "novice": {
            "what": "同一笔退款被入账两次",
            "why": "没有按 refund_id 去重",
            "fix": "只在第一次回调时入账",
        },
    },
    {
        "id": "c06",
        "index": 6,
        "title": "批量结算漏页",
        "file": "fintech.c",
        "function": "settle_batch",
        "strategy": "移除提前 break，处理完所有页",
        "old": """    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    long total = 0;
    for (int i = 0; i < 3; i++) {
        total += pages[i];
        if (i == 1) {
            break;
        }
    }""",
        "new": """    long total = 0;
    for (int i = 0; i < 3; i++) {
        total += pages[i];
    }""",
        "novice": {
            "what": "结算只处理了前两页",
            "why": "循环在第二页提前退出",
            "fix": "移除提前 break",
        },
    },
    {
        "id": "c07",
        "index": 7,
        "title": "账本部分写入",
        "file": "fintech.c",
        "function": "append_entries",
        "strategy": "失败时返回 0，不提交批次",
        "old": """    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    long written = 0;
    for (int i = 0; i < 3; i++) {
        if (i == fail_at) {
            return written;
        }
        entries[i] = 1;
        written++;
    }""",
        "new": """    long written = 0;
    for (int i = 0; i < 3; i++) {
        if (i == fail_at) {
            return 0;
        }
        entries[i] = 1;
        written++;
    }""",
        "novice": {
            "what": "失败批次留下半截账本",
            "why": "失败时返回已写入条数，调用方误以为成功",
            "fix": "失败返回 0，整批回滚",
        },
    },
    {
        "id": "c08",
        "index": 8,
        "title": "欧元出款走错通道",
        "file": "fintech.c",
        "function": "route_payout",
        "strategy": "按币种选择本地清算通道",
        "old": """    if (strcmp(currency, "USD") == 0) {
        return "us-bank";
    }
    return "default-bank";""",
        "new": """    if (strcmp(currency, "USD") == 0) {
        return "us-bank";
    }
    if (strcmp(currency, "EUR") == 0) {
        return "eu-bank";
    }
    if (strcmp(currency, "GBP") == 0) {
        return "uk-bank";
    }
    return "default-bank";""",
        "novice": {
            "what": "欧元出款被路由到默认通道",
            "why": "只识别 USD",
            "fix": "增加 EUR/GBP 本地通道",
        },
    },
    {
        "id": "c09",
        "index": 9,
        "title": "风控超时阻断支付",
        "file": "fintech.c",
        "function": "fraud_score",
        "strategy": "超时返回降级风险分 50",
        "old": """    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    if (timeout) {
        return -1;
    }""",
        "new": """    if (timeout) {
        return 50;
    }""",
        "novice": {
            "what": "风控超时导致整条支付失败",
            "why": "超时返回 -1，调用方直接拒绝",
            "fix": "返回中间风险分 50，标记降级",
        },
    },
    {
        "id": "c10",
        "index": 10,
        "title": "结算日落在周末",
        "file": "fintech.c",
        "function": "next_settlement_date",
        "strategy": "跳过周六/周日",
        "old": """    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    return day + 1;""",
        "new": """    int next = day + 1;
    while (next % 7 == 5 || next % 7 == 6) {
        next++;
    }
    return next;""",
        "novice": {
            "what": "周五结算被排到周六",
            "why": "日期只加 1 天",
            "fix": "循环跳过周末",
        },
    },
]


def _perf() -> str:
    path = os.environ.get("PERF") or shutil.which("perf")
    if not path:
        raise RuntimeError("perf not found; set PERF or install linux-tools")
    return path


def _perf_env() -> dict[str, str]:
    env = os.environ.copy()
    lib = os.environ.get("PERF_LIB")
    if lib:
        env["LD_LIBRARY_PATH"] = lib + (
            ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )
    return env


def _run(command: list[str], cwd: Path, timeout: int = 90) -> dict[str, Any]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_perf_env(),
    )
    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "output_tail": "\n".join((result.stdout + result.stderr).strip().splitlines()[-6:]),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _build(work: Path) -> None:
    common = ["gcc", "-g", "-O0", "-fno-inline", "-Wall", "-Wextra"]
    subprocess.run(
        [*common, "-o", "fintech_tests", "fintech.c", "tests.c"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [*common, "-o", "fintech_perf", "fintech.c", "perf_harness.c"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_tests(work: Path, case_index: int | None = None) -> dict[str, Any]:
    target = [str(work / "fintech_tests")]
    if case_index is not None:
        target.append(str(case_index))
    return _run(target, work)


def _capture(
    work: Path, case: dict[str, Any], tag: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    perf = _perf()
    env = _perf_env()
    harness = work / "fintech_perf"
    process = subprocess.Popen(
        [str(harness), str(case["index"])],
        cwd=work,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    time.sleep(0.25)
    data_path = work / f"{case['id']}-{tag}.perf.data"
    stack_path = work / f"{case['id']}-{tag}.stacks.txt"
    record = subprocess.run(
        [
            perf,
            "record",
            "-F",
            "99",
            "-g",
            "--call-graph",
            "dwarf",
            "-p",
            str(process.pid),
            "-o",
            str(data_path),
            "--",
            "sleep",
            "0.8",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    script = subprocess.run(
        [
            perf,
            "script",
            "-i",
            str(data_path),
            "-F",
            "comm,pid,period,ip,sym,dso,srcline",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    stack_path.write_text(script.stdout, encoding="utf-8")
    samples = load_stacks(stack_path, case_id=case["id"])
    capture = {
        "tag": tag,
        "record": {
            "returncode": record.returncode,
            "output_tail": "\n".join((record.stdout + record.stderr).strip().splitlines()[-3:]),
        },
        "script": {
            "returncode": script.returncode,
            "bytes": len(script.stdout),
        },
        "samples": len(samples),
        "stacks_path": str(stack_path),
    }
    return samples, capture


def run_real_perf_rea(max_iterations: int = 10) -> dict[str, Any]:
    if os.name != "posix":
        return {
            "status": "skipped",
            "reason": "real perf capture requires Linux (this host is Windows)",
            "cases": 0,
            "fixed": 0,
        }
    perf = _perf()
    cases = CASES[:max_iterations]
    with tempfile.TemporaryDirectory(prefix="opsgraph-real-perf-") as tmp:
        work = Path(tmp) / "fintech_c"
        shutil.copytree(C_ROOT, work, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _build(work)
        before_all = _run_tests(work)
        iterations = []
        for case in cases:
            samples, before_capture = _capture(work, case, "before")
            rca = analyze_code(samples, work, case)
            candidate = PatchCandidate(
                candidate_id=f"{case['id']}-reviewed",
                strategy=case["strategy"],
                old=case["old"],
                new=case["new"],
                rationale="reviewed C patch",
            )
            applied = apply_candidate(work, case["file"], candidate)
            _build(work)
            test = _run_tests(work, case["index"])
            fixed = applied["applied"] and test["passed"]
            after_capture = {"samples": 0}
            if fixed:
                _, after_capture = _capture(work, case, "after")
            else:
                restore_candidate(work, case["file"], candidate)
                _build(work)
            iterations.append(
                {
                    "iteration": len(iterations) + 1,
                    "case_id": case["id"],
                    "title": case["title"],
                    "rca": rca,
                    "patch": {
                        "candidate_id": candidate.candidate_id,
                        "strategy": candidate.strategy,
                        "safety": applied["safety"],
                        "applied": applied["applied"],
                    },
                    "test": test,
                    "before_capture": before_capture,
                    "after_capture": after_capture,
                    "status": "fixed" if fixed else "failed",
                    "novice": case["novice"],
                }
            )
        final_all = _run_tests(work)
    fixed = sum(1 for item in iterations if item["status"] == "fixed")
    return {
        "schema_version": 1,
        "source": "linux.perf + code RCA + REA",
        "perf": Path(perf).name,
        "cases": len(iterations),
        "fixed": fixed,
        "success_rate": round(fixed / len(iterations), 3) if iterations else 0.0,
        "before_all": before_all,
        "final_all": final_all,
        "iterations": iterations,
        "summary": f"{fixed}/{len(iterations)} C 金融故障通过真实 perf 栈定位并修复。",
    }


def write_real_perf_artifacts(result: dict[str, Any], root: Path | None = None) -> dict[str, str]:
    target = Path(root) if root else PROJECT_ROOT / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    json_path = write_json(target / "real_perf_rea.json", result)
    report_path = target / "real-perf-rea.md"
    lines = [
        "# 真实 perf RCA / REA 报告",
        "",
        f"> {result.get('summary', '')}",
        "",
        f"- perf：`{result.get('perf', '')}`",
        f"- 用例：{result.get('cases', 0)}",
        f"- 修复：{result.get('fixed', 0)}",
        f"- 最终 C 回归：{'PASS' if (result.get('final_all') or {}).get('passed') else 'FAIL'}",
        "",
        "| # | 故障 | 根因 | 栈样本 | C 回归 | 修复后样本 |",
        "|---:|---|---|---:|---|---:|",
    ]
    for item in result.get("iterations", []):
        finding = item["rca"].get("finding", {})
        lines.append(
            f"| {item['iteration']} | {item['title']} | `{finding.get('file')}:{finding.get('line')}` "
            f"`{finding.get('function')}` | {item['before_capture'].get('samples', 0)} | "
            f"{'pass' if item['test'].get('passed') else 'fail'} | {item['after_capture'].get('samples', 0)} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "report": str(report_path)}
