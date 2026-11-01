"""Small shared helpers."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable


def ensure_utf8_stdout() -> None:
    """Make Chinese output safe on a GBK Windows console."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target


def write_text(path: str | Path, value: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8", newline="\n")
    return target


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def mean(values: Iterable[float]) -> float:
    items = [float(value) for value in values]
    return sum(items) / len(items) if items else 0.0


def median(values: Iterable[float]) -> float:
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    middle = len(items) // 2
    if len(items) % 2:
        return items[middle]
    return (items[middle - 1] + items[middle]) / 2.0


def mad(values: Iterable[float], center: float | None = None) -> float:
    items = [float(value) for value in values]
    if not items:
        return 0.0
    pivot = median(items) if center is None else center
    return median(abs(value - pivot) for value in items)


def robust_z(value: float, baseline: list[float], floor_ratio: float = 0.03) -> float:
    """Robust z-score that still works for near-constant baselines.

    Ops telemetry often has a constant baseline (``MAD == 0``). Falling back to
    a percentage-of-median floor keeps a small but real change from looking
    infinitely anomalous, and keeps the score comparable across signals.
    """

    center = median(baseline)
    spread = mad(baseline, center) * 1.4826
    floor = max(abs(center) * floor_ratio, 1e-9)
    spread = max(spread, floor)
    return (value - center) / spread


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def fmt_ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f}s"
    return f"{value:.0f}ms"


def fmt_qps(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def percentile(values: Iterable[float], pct: float) -> float:
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    rank = (len(items) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return items[low]
    return items[low] + (items[high] - items[low]) * (rank - low)
