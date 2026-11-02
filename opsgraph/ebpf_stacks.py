"""Adapters from real eBPF stack outputs into the code-RCA sample schema.

Two common text formats are supported:

* folded/collapsed stacks: ``frame;frame 42`` (bpftrace, stackcollapse)
* ``perf script`` blocks with optional ``file.py:line`` source lines

This keeps the RCA engine independent from a specific collector. On Linux,
capture with ``perf record -F 99 -g --call-graph dwarf`` or a bpftrace uprobe,
then feed the text through this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_RE = re.compile(r"([\w./\\-]+\.(?:py|go|java|js|ts)):(\d+)")
FRAME_FILE_RE = re.compile(r"^(.*?)\s*\(([^()]+):(\d+)\)$")


def _frame(symbol: str, file: str = "", line: int = 0) -> dict[str, Any]:
    return {"symbol": symbol.strip(), "file": file, "line": int(line)}


def parse_folded_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    try:
        stack_text, count_text = text.rsplit(" ", 1)
        count = int(count_text)
    except ValueError:
        return None
    frames = []
    for part in stack_text.split(";"):
        part = part.strip()
        if not part:
            continue
        match = FRAME_FILE_RE.match(part)
        if match:
            frames.append(_frame(match.group(1), match.group(2), int(match.group(3))))
            continue
        source = SOURCE_RE.search(part)
        if source:
            frames.append(
                _frame(
                    part[: source.start()].strip() or source.group(1),
                    source.group(1),
                    int(source.group(2)),
                )
            )
        else:
            frames.append(_frame(part))
    if not frames:
        return None
    return {"stack": frames, "count": max(1, count)}


def parse_collapsed(
    text: str, case_id: str = "external", phase: str = "incident"
) -> list[dict[str, Any]]:
    samples = []
    index = 0
    for line in text.splitlines():
        parsed = parse_folded_line(line)
        if not parsed:
            continue
        for _ in range(min(parsed["count"], 1000)):
            samples.append(
                {
                    "id": f"{case_id}-folded-{index:04d}",
                    "case_id": case_id,
                    "phase": phase,
                    "kind": "oncpu",
                    "timestamp": 0,
                    "pid": 9000 + (index % 9),
                    "comm": "python",
                    "duration_us": 1000,
                    "error": False,
                    "stack": parsed["stack"],
                    "probe": "ebpf.folded",
                }
            )
            index += 1
    return samples


def parse_perf_script(
    text: str, case_id: str = "external", phase: str = "incident"
) -> list[dict[str, Any]]:
    samples = []
    blocks = re.split(r"\n\s*\n", text)
    for block_index, block in enumerate(blocks):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header = lines[0]
        pid_match = re.search(r"\s(\d+)\s", header)
        pid = int(pid_match.group(1)) if pid_match else 0
        frames: list[dict[str, Any]] = []
        for line in lines[1:]:
            stripped = line.strip()
            source = SOURCE_RE.search(stripped)
            if source and frames:
                frames[-1]["file"] = source.group(1)
                frames[-1]["line"] = int(source.group(2))
                continue
            if source:
                frames.append(_frame(source.group(1), source.group(1), int(source.group(2))))
                continue
            if re.match(r"^[0-9a-f]{8,}\s", stripped) or re.match(r"^[\w.$@<>+~-]+\+0x", stripped):
                symbol = stripped.split()[0]
                frames.append(_frame(symbol))
                continue
            if stripped and not stripped.startswith("["):
                frames.append(_frame(stripped))
        if not frames:
            continue
        samples.append(
            {
                "id": f"{case_id}-perf-{block_index:04d}",
                "case_id": case_id,
                "phase": phase,
                "kind": "oncpu",
                "timestamp": 0,
                "pid": pid,
                "comm": header.split()[0] if header.split() else "unknown",
                "duration_us": 1000,
                "error": False,
                "stack": frames,
                "probe": "ebpf.perf",
            }
        )
    return samples


def load_stacks(
    path: str | Path, case_id: str = "external", phase: str = "incident"
) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if any(";" in line for line in text.splitlines() if line.strip() and not line.startswith("#")):
        return parse_collapsed(text, case_id, phase)
    return parse_perf_script(text, case_id, phase)
