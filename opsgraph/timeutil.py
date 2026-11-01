"""Scenario time helpers (fixed UTC+8, no DST)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import SCENARIO_START

CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


def ts(day_offset_minutes: int) -> int:
    """Minute offset from the scenario anchor -> unix seconds."""

    return SCENARIO_START + day_offset_minutes * 60


def iso(value: int) -> str:
    return datetime.fromtimestamp(value, CST).isoformat()


def clock(value: int) -> str:
    return datetime.fromtimestamp(value, CST).strftime("%H:%M")


def clock_seconds(value: int) -> str:
    return datetime.fromtimestamp(value, CST).strftime("%H:%M:%S")


def day(value: int) -> str:
    return datetime.fromtimestamp(value, CST).strftime("%Y-%m-%d")


def minute_index(value: int) -> int:
    return int((value - SCENARIO_START) // 60)


def parse_iso(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())
