"""Project-wide constants and defaults."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "model"
DATA_DIR = PROJECT_ROOT / "data"
REPLAY_DIR = DATA_DIR / "replay"
AGENTSIGHT_SNAPSHOT = REPLAY_DIR / "agentsight_snapshot.json"
ARTIFACT_DIR = PROJECT_ROOT / "artifacts"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"

WORKSPACE = os.environ.get("OPSGRAPH_WORKSPACE", "opsgraph")
UMODEL_ADDR = os.environ.get("OPSGRAPH_UMODEL_ADDR", "http://127.0.0.1:18080")
CONSOLE_HOST = os.environ.get("OPSGRAPH_CONSOLE_HOST", "127.0.0.1")
CONSOLE_PORT = int(os.environ.get("OPSGRAPH_CONSOLE_PORT", "18088"))

#: The replay scenario is anchored at 2026-11-02 00:00:00 Asia/Shanghai.
SCENARIO_START = 1793548800
WINDOW_START = int(os.environ.get("OPSGRAPH_WINDOW_START", str(SCENARIO_START)))
WINDOW_MINUTES = int(os.environ.get("OPSGRAPH_WINDOW_MINUTES", "60"))
WINDOW_SECONDS = WINDOW_MINUTES * 60
WINDOW_END = WINDOW_START + WINDOW_SECONDS

#: Incident metadata used by the console and the report.
INCIDENT_ID = "INC-2026-1102-01"
VICTIM_SERVICE = "payment-gateway"
ROOT_CAUSE_SERVICE = "checkout-service"
