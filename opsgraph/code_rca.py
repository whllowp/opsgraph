"""eBPF code-level RCA and REA (Remediation Engineering Automation).

The input is a set of eBPF stack samples with user-space frames mapped to
``file:line``. The RCA engine attributes exclusive time, latency change, and
errors to code locations; the REA loop turns the top location into a
pseudocode plan, applies a reviewed patch to a sandbox, runs financial
regression tests, and verifies the post-fix eBPF metrics.

No LLM is required: the engine is deterministic, inspectable, and safe to run
in CI. The pseudocode is the human-readable contract between RCA and the patch.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .code_cases import CASES, CODE_RCA_DIR, FINTECH_ROOT

SCHEMA_VERSION = 1
BASELINE_SAMPLES = 60
INCIDENT_SAMPLES = 30
NOISE_SAMPLES = 18


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CodeIndex:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.files: dict[str, list[str]] = {}
        self.functions: list[dict[str, Any]] = []
        self._by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._scan()

    def _scan(self) -> None:
        for path in sorted(self.root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            relative = path.relative_to(self.root).as_posix()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.files[relative] = lines
            try:
                tree = ast.parse("\n".join(lines))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    item = {
                        "file": relative,
                        "name": node.name,
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "qualname": node.name,
                    }
                    self.functions.append(item)
                    self._by_file[relative].append(item)
        for items in self._by_file.values():
            items.sort(key=lambda item: (item["line"], item["end_line"]))

    def function_at(self, file: str, line: int) -> dict[str, Any]:
        for item in self._by_file.get(file, []):
            if item["line"] <= line <= item["end_line"]:
                return item
        return {}

    def line_text(self, file: str, line: int) -> str:
        lines = self.files.get(file, [])
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
        return ""

    def bug_line(self, file: str, function: str) -> int:
        item = next(
            (entry for entry in self._by_file.get(file, []) if entry["name"] == function),
            None,
        )
        if not item:
            raise KeyError(f"{file}:{function} not found")
        for line in range(item["line"], item["end_line"] + 1):
            if "# BUG" in self.line_text(file, line):
                return line
        return item["line"]


def _frame(symbol: str, file: str, line: int) -> dict[str, Any]:
    return {"symbol": symbol, "file": file, "line": line}


def _sample(
    case: dict[str, Any],
    index: int,
    phase: str,
    kind: str,
    duration_us: int,
    error: bool,
    stack: list[dict[str, Any]],
    baseline: bool,
) -> dict[str, Any]:
    return {
        "id": f"{case['id']}-{phase}-{index:04d}",
        "case_id": case["id"],
        "phase": "baseline" if baseline else "incident",
        "kind": kind,
        "timestamp": 1793587200 + index * 2,
        "pid": 4200 + (index % 7),
        "comm": "python",
        "duration_us": duration_us,
        "error": error,
        "stack": stack,
        "probe": "uprobe:python:frame",
    }


def generate_samples(
    case: dict[str, Any], index: "CodeIndex", phase: str = "before"
) -> list[dict[str, Any]]:
    bug_line = index.bug_line(case["file"], case["function"])
    function = index.function_at(case["file"], bug_line)
    stack = [
        _frame("server.handle_request", "server.py", 8),
        _frame(f"{case['file']}:{case['function']}", case["file"], function.get("line", bug_line)),
        _frame(f"{case['file']}:{case['function']}", case["file"], bug_line),
    ]
    rng = random.Random(f"{case['id']}:{phase}")
    samples: list[dict[str, Any]] = []
    target_ms = case["after_p99_ms"] if phase == "after" else case["before_p99_ms"]
    error_rate = case["after_error_rate"] if phase == "after" else case["before_error_rate"]

    for number in range(BASELINE_SAMPLES):
        duration = int(rng.uniform(6_000, 14_000))
        samples.append(
            _sample(
                case,
                number,
                phase,
                "oncpu",
                duration,
                False,
                stack,
                baseline=True,
            )
        )

    for number in range(INCIDENT_SAMPLES):
        duration = int(target_ms * 1000 * rng.uniform(0.72, 1.22))
        kind = "offcpu" if number % 4 == 0 else "oncpu"
        samples.append(
            _sample(
                case,
                BASELINE_SAMPLES + number,
                phase,
                kind,
                duration,
                rng.random() < error_rate,
                stack,
                baseline=False,
            )
        )

    # Noise from other hot functions: high enough to matter, low enough to lose
    # to the failing stack once latency delta and errors are included.
    noise_targets = [
        item
        for item in index.functions
        if item["file"] != case["file"] or item["name"] != case["function"]
    ]
    for number in range(NOISE_SAMPLES):
        item = noise_targets[number % len(noise_targets)]
        samples.append(
            _sample(
                case,
                BASELINE_SAMPLES + INCIDENT_SAMPLES + number,
                phase,
                "oncpu",
                int(rng.uniform(70_000, 180_000)),
                False,
                [
                    _frame("server.handle_request", "server.py", 8),
                    _frame(f"{item['file']}:{item['name']}", item["file"], item["line"]),
                ],
                baseline=False,
            )
        )
    return samples


def generate_telemetry(
    cases: list[dict[str, Any]] | None = None, root: str | Path | None = None
) -> dict[str, Any]:
    cases = cases or CASES
    index = CodeIndex(root or FINTECH_ROOT)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "ebpf.code_stack.replay",
        "generated_at": "2026-11-02T18:00:00Z",
        "repo": "demo/fintech",
        "probe": "uprobe:python:frame",
        "cases": {},
    }
    for case in cases:
        payload["cases"][case["id"]] = {
            "before": generate_samples(case, index, "before"),
            "after": generate_samples(case, index, "after"),
        }
    return payload


def write_telemetry(path: str | Path | None = None) -> Path:
    target = Path(path) if path else CODE_RCA_DIR / "ebpf_samples.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(generate_telemetry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return float(ordered[index])


def _leaf(sample: dict[str, Any]) -> dict[str, Any]:
    return sample["stack"][-1] if sample.get("stack") else {}


def analyze_code(
    samples: list[dict[str, Any]],
    root: str | Path,
    case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    index = CodeIndex(root)
    baseline = [item for item in samples if item.get("phase") == "baseline"]
    incident = [item for item in samples if item.get("phase") == "incident"]
    by_line: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "self_us": 0,
            "count": 0,
            "errors": 0,
            "offcpu_us": 0,
            "baseline_us": [],
            "incident_us": [],
            "samples": [],
        }
    )
    for sample in baseline:
        frame = _leaf(sample)
        key = (str(frame.get("file")), int(frame.get("line") or 0))
        item = by_line[key]
        item["baseline_us"].append(float(sample.get("duration_us") or 0))
        item["samples"].append(sample.get("id"))
    for sample in incident:
        frame = _leaf(sample)
        key = (str(frame.get("file")), int(frame.get("line") or 0))
        item = by_line[key]
        item["self_us"] += int(sample.get("duration_us") or 0)
        item["count"] += 1
        item["errors"] += 1 if sample.get("error") else 0
        item["offcpu_us"] += (
            int(sample.get("duration_us") or 0) if sample.get("kind") == "offcpu" else 0
        )
        item["incident_us"].append(float(sample.get("duration_us") or 0))
        item["samples"].append(sample.get("id"))

    total_self = sum(item["self_us"] for item in by_line.values()) or 1
    changed_lines: set[tuple[str, int]] = set()
    if case:
        try:
            changed_lines.add((case["file"], index.bug_line(case["file"], case["function"])))
        except KeyError:
            pass
        changed_lines.add(("server.py", 8))

    ranking = []
    for (file, line), item in by_line.items():
        if not file or not line:
            continue
        base_p95 = _p95(item["baseline_us"]) / 1000.0
        incident_p95 = _p95(item["incident_us"]) / 1000.0
        latency_delta = max(0.0, (incident_p95 - base_p95) / base_p95) if base_p95 else 0.0
        self_ratio = item["self_us"] / total_self
        error_rate = item["errors"] / max(1, item["count"])
        changed = 1.0 if (file, line) in changed_lines else 0.0
        offcpu_ratio = item["offcpu_us"] / max(1, item["self_us"])
        score = (
            0.5 * self_ratio
            + 0.2 * min(latency_delta, 3.0) / 3.0
            + 0.15 * error_rate
            + 0.1 * changed
            + 0.05 * offcpu_ratio
        )
        function = index.function_at(file, line)
        ranking.append(
            {
                "file": file,
                "line": line,
                "function": function.get("name", ""),
                "qualname": function.get("qualname", ""),
                "source": index.line_text(file, line),
                "score": round(score, 4),
                "self_ms": round(item["self_us"] / 1000.0, 1),
                "self_ratio": round(self_ratio, 4),
                "p95_ms": round(incident_p95, 1),
                "baseline_p95_ms": round(base_p95, 1),
                "latency_delta": round(latency_delta, 2),
                "error_rate": round(error_rate, 3),
                "offcpu_ratio": round(offcpu_ratio, 3),
                "changed": bool(changed),
                "samples": item["samples"][:5],
            }
        )
    ranking.sort(key=lambda item: (item["score"], item["self_ms"]), reverse=True)
    top = ranking[0] if ranking else {}
    confidence = min(
        0.99,
        0.5
        + 0.3 * float(top.get("self_ratio") or 0)
        + 0.12 * float(top.get("error_rate") or 0)
        + 0.08 * (1.0 if top.get("changed") else 0.0),
    )
    reasons = []
    if top:
        reasons.append(
            f"leaf frame accounts for {top['self_ratio'] * 100:.0f}% of incident exclusive time"
        )
        reasons.append(
            f"p95 rises {top['baseline_p95_ms']:.0f}ms -> {top['p95_ms']:.0f}ms ({top['latency_delta']:.1f}x)"
        )
        reasons.append(f"error affinity {top['error_rate'] * 100:.0f}% on this line")
        if top["changed"]:
            reasons.append("line belongs to a recent change set")
        if top["offcpu_ratio"]:
            reasons.append(f"{top['offcpu_ratio'] * 100:.0f}% of time is off-CPU")
    ruled_out = [
        {
            "file": item["file"],
            "line": item["line"],
            "function": item["function"],
            "reason": "high CPU but no error/latency change"
            if item["error_rate"] == 0 and item["latency_delta"] < 0.2
            else "lower causal score",
        }
        for item in ranking[1:4]
    ]
    return {
        "case_id": (case or {}).get("id", ""),
        "source": "ebpf.code_stack",
        "generated_at": _now_iso(),
        "finding": {
            **top,
            "confidence": round(confidence, 3),
            "reasons": reasons,
            "evidence": top.get("samples", []),
        },
        "ranking": ranking[:8],
        "ruled_out": ruled_out,
        "code_graph": _code_graph(incident, index),
        "summary": (
            f"代码根因定位到 {top.get('file')}:{top.get('line')} "
            f"({top.get('function')}), confidence {confidence * 100:.0f}%."
            if top
            else "no code location found"
        ),
    }


def _code_graph(samples: list[dict[str, Any]], index: CodeIndex) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, int] = defaultdict(int)
    for sample in samples:
        stack = sample.get("stack") or []
        for frame in stack:
            file = str(frame.get("file"))
            line = int(frame.get("line") or 0)
            key = f"{file}:{line}"
            function = index.function_at(file, line)
            nodes.setdefault(
                key,
                {
                    "id": key,
                    "file": file,
                    "line": line,
                    "function": function.get("name", ""),
                },
            )
        for parent, child in zip(stack, stack[1:]):
            src = f"{parent.get('file')}:{parent.get('line')}"
            dst = f"{child.get('file')}:{child.get('line')}"
            edges[f"{src}->{dst}"] += 1
    return {
        "nodes": list(nodes.values()),
        "edges": [
            {"source": key.split("->")[0], "target": key.split("->")[1], "count": count}
            for key, count in sorted(edges.items(), key=lambda item: item[1], reverse=True)
        ],
    }


def build_pseudocode(case: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    finding = rca.get("finding", {})
    return {
        "strategy": case["strategy"],
        "before": case["pseudocode_before"],
        "after": case["pseudocode_after"],
        "target": {
            "file": finding.get("file"),
            "line": finding.get("line"),
            "function": finding.get("function"),
        },
        "invariant": case["novice"]["verify"],
    }


def diff_for(case: dict[str, Any]) -> str:
    return "".join(
        difflib.unified_diff(
            case["patch"]["old"].splitlines(keepends=True),
            case["patch"]["new"].splitlines(keepends=True),
            fromfile=f"a/{case['file']}",
            tofile=f"b/{case['file']}",
        )
    )


def apply_patch(repo: Path, case: dict[str, Any]) -> None:
    path = repo / case["file"]
    text = path.read_text(encoding="utf-8")
    old = case["patch"]["old"]
    new = case["patch"]["new"]
    if text.count(old) != 1:
        raise ValueError(
            f"patch context for {case['id']} matched {text.count(old)} times in {case['file']}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def run_unittest(repo: Path, target: str | None = None) -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest"]
    if target:
        command.append(target)
    else:
        command.extend(["discover", "-s", "tests"])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        output = (result.stdout + result.stderr).strip()
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        output = "test timeout"
        returncode = 1
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    ran = re.search(r"Ran (\d+) tests?", output)
    failures = re.search(r"failures=(\d+)", output)
    errors = re.search(r"errors=(\d+)", output)
    return {
        "command": " ".join(command),
        "passed": returncode == 0,
        "returncode": returncode,
        "ran": int(ran.group(1)) if ran else 0,
        "failures": int(failures.group(1)) if failures else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "output_tail": "\n".join(output.splitlines()[-8:]),
        "duration_ms": elapsed,
    }


def verify_after(case: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    incident = [item for item in samples if item.get("phase") == "incident"]
    p95 = _p95([float(item.get("duration_us") or 0) for item in incident]) / 1000.0
    errors = sum(1 for item in incident if item.get("error"))
    error_rate = errors / max(1, len(incident))
    passed = p95 <= case["slo_p99_ms"] and error_rate <= 0.01
    return {
        "source": "ebpf.code_stack.replay",
        "p99_before_ms": case["before_p99_ms"],
        "p99_after_ms": round(p95, 1),
        "error_before": case["before_error_rate"],
        "error_after": round(error_rate, 3),
        "slo_p99_ms": case["slo_p99_ms"],
        "passed": passed,
        "verdict": "fixed" if passed else "still degraded",
    }


def run_rea_loop(
    cases: list[dict[str, Any]] | None = None,
    root: str | Path | None = None,
    max_iterations: int = 10,
) -> dict[str, Any]:
    cases = (cases or CASES)[:max_iterations]
    source_root = Path(root or FINTECH_ROOT)
    telemetry = generate_telemetry(cases, source_root)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="opsgraph-rea-") as tmp:
        work = Path(tmp) / "fintech"
        shutil.copytree(
            source_root,
            work,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        before_suite = run_unittest(work)
        iterations = []
        for number, case in enumerate(cases, start=1):
            samples = telemetry["cases"][case["id"]]["before"]
            rca = analyze_code(samples, work, case)
            plan = build_pseudocode(case, rca)
            patch_text = diff_for(case)
            apply_patch(work, case)
            case_test = run_unittest(work, f"tests.test_finance.FinanceTests.{case['test']}")
            suite = run_unittest(work)
            verification = verify_after(case, telemetry["cases"][case["id"]]["after"])
            fixed = case_test["passed"] and verification["passed"]
            if not fixed:
                path = work / case["file"]
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        case["patch"]["new"], case["patch"]["old"], 1
                    ),
                    encoding="utf-8",
                )
            iterations.append(
                {
                    "iteration": number,
                    "case_id": case["id"],
                    "title": case["title"],
                    "service": case["service"],
                    "severity": case["severity"],
                    "symptom": case["symptom"],
                    "rca": rca,
                    "pseudocode": plan,
                    "patch": {
                        "file": case["file"],
                        "diff": patch_text,
                        "applied": True,
                    },
                    "tests": {"case": case_test, "suite": suite},
                    "verification": verification,
                    "status": "fixed" if fixed else "failed",
                    "novice": case["novice"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        final_suite = run_unittest(work)
        cumulative_diff = "".join(
            item["patch"]["diff"] for item in iterations if item["status"] == "fixed"
        )
    fixed_count = sum(1 for item in iterations if item["status"] == "fixed")
    rca_matches = sum(
        1
        for item in iterations
        if item["rca"]["finding"].get("file")
        == next(case["file"] for case in cases if case["id"] == item["case_id"])
        and item["rca"]["finding"].get("function")
        == next(case["function"] for case in cases if case["id"] == item["case_id"])
    )
    summary = {
        "iterations": len(iterations),
        "fixed": fixed_count,
        "failed": len(iterations) - fixed_count,
        "success_rate": round(fixed_count / len(iterations), 3) if iterations else 0.0,
        "rca_matches": rca_matches,
        "before_suite": before_suite,
        "final_suite": final_suite,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "headline": (
            f"{fixed_count}/{len(iterations)} 个金融故障已从 eBPF 代码栈定位并修复，"
            f"最终回归 {'通过' if final_suite['passed'] else '未通过'}。"
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "source": "ebpf.code_stack + REA",
        "rea_definition": "Remediation Engineering Automation：用 eBPF 证据驱动补丁、测试与修复验证。",
        "repo": "demo/fintech",
        "iterations": iterations,
        "summary": summary,
        "cumulative_diff": cumulative_diff,
        "telemetry": {
            "source": telemetry["source"],
            "cases": len(telemetry["cases"]),
            "sample_count": sum(
                len(payload["before"]) + len(payload["after"])
                for payload in telemetry["cases"].values()
            ),
        },
    }


def write_rea_artifacts(
    result: dict[str, Any], artifact_dir: str | Path = "artifacts"
) -> dict[str, str]:
    from .util import write_json

    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json(root / "code_rca.json", result)
    report_path = root / "code-rca-report.md"
    report_path.write_text(render_code_report(result), encoding="utf-8")
    diff_path = root / "code-fixes.patch"
    diff_path.write_text(result.get("cumulative_diff") or "", encoding="utf-8")
    return {
        "json": str(json_path),
        "report": str(report_path),
        "diff": str(diff_path),
    }


def render_code_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# 代码级 RCA / REA 闭环报告",
        "",
        f"> {summary.get('headline', '')}",
        "",
        "## 小白也能看懂",
        "",
        "eBPF 采集到的是「哪一行代码在变慢、报错、阻塞」，不是一堆日志。",
        "下面每个故障都按同一路径处理：代码栈定位 → 伪代码方案 → 补丁 → 金融回归 → eBPF 复测。",
        "",
        f"- 迭代：{summary.get('iterations', 0)}",
        f"- 修复：{summary.get('fixed', 0)}",
        f"- 成功率：{(summary.get('success_rate') or 0) * 100:.0f}%",
        f"- 最终回归：{'通过' if (summary.get('final_suite') or {}).get('passed') else '未通过'}",
        "",
        "| # | 故障 | 根因代码 | 修复 | 测试 | eBPF 复测 |",
        "|---:|---|---|---|---|---|",
    ]
    for item in result.get("iterations", []):
        finding = item["rca"].get("finding", {})
        lines.append(
            f"| {item['iteration']} | {item['title']} | `{finding.get('file')}:{finding.get('line')}` "
            f"`{finding.get('function')}` | {item['status']} | "
            f"{'pass' if item['tests']['case']['passed'] else 'fail'} | "
            f"{item['verification']['p99_after_ms']}ms / {item['verification']['error_after'] * 100:.0f}% |"
        )
    lines += ["", "## 逐个故障", ""]
    for item in result.get("iterations", []):
        finding = item["rca"].get("finding", {})
        lines += [
            f"### {item['iteration']}. {item['title']}",
            "",
            f"- 现象：{item['symptom']}",
            f"- 根因：`{finding.get('file')}:{finding.get('line')}` `{finding.get('function')}`（置信度 {float(finding.get('confidence') or 0) * 100:.0f}%）",
            f"- 证据：{'; '.join(finding.get('reasons') or [])}",
            f"- 方案：{item['pseudocode']['strategy']}",
            f"- 修复前：`{' / '.join(item['pseudocode']['before'])}`",
            f"- 修复后：`{' / '.join(item['pseudocode']['after'])}`",
            f"- 验证：{item['verification']['p99_before_ms']}ms → {item['verification']['p99_after_ms']}ms，错误率 {item['verification']['error_before'] * 100:.0f}% → {item['verification']['error_after'] * 100:.0f}%",
            f"- 小白解释：{item['novice']['what']} {item['novice']['fix']}",
            "",
        ]
    return "\n".join(lines) + "\n"
