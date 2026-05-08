#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


PROJECT_DIR = _env_path("AWP_PREDICT_PROJECT_DIR", "/srv/awp-predict")
OPENCLAW_HOME = _env_path("AWP_PREDICT_OPENCLAW_HOME", str(PROJECT_DIR))
OPENCLAW_BIN = os.environ.get(
    "AWP_PREDICT_OPENCLAW_BIN",
    str(OPENCLAW_HOME / ".openclaw" / "bin" / "openclaw"),
)

MINER_ENV = _env_path("AWP_PREDICT_MINER_ENV", "/srv/awp-miner/.env")
WALLET_BIN = os.environ.get(
    "AWP_WALLET_BIN",
    "/srv/awp-miner/vendor/node/bin/awp-wallet",
)
WALLET_HOME = _env_path("AWP_WALLET_HOME", "/srv/awp-miner/.wallet")
WALLET_ID = os.environ.get("AWP_AGENT_ID", "awp-miner")

PREDICT_SERVER_URL = os.environ.get("PREDICT_SERVER_URL", "https://api.agentpredict.work")
DEFAULT_MODEL = os.environ.get("AWP_PREDICT_MODEL", "gpt-5.4")
DEFAULT_BASE_URL = os.environ.get("AWP_PREDICT_BASE_URL", "https://cdk.muyuai.top/v1")
DEFAULT_INTERVAL = int(os.environ.get("PREDICT_LOOP_INTERVAL_DEFAULT", "120") or "120")

MONITOR_HOST = os.environ.get("AWP_PREDICT_MONITOR_HOST", "127.0.0.1")
MONITOR_PORT = int(os.environ.get("AWP_PREDICT_MONITOR_PORT", "8791") or "8791")

SERVICE_GATEWAY = os.environ.get("AWP_PREDICT_GATEWAY_SERVICE", "awp-predict-gateway")
SERVICE_LOOP = os.environ.get("AWP_PREDICT_LOOP_SERVICE", "awp-predict-loop")
SERVICE_MONITOR = os.environ.get("AWP_PREDICT_MONITOR_SERVICE", "awp-predict-monitor")
JOURNAL_LOOP_UNIT = os.environ.get("AWP_PREDICT_LOOP_JOURNAL_UNIT", SERVICE_LOOP)


def predict_path_prefix() -> str:
    return f"{PROJECT_DIR}:{Path(WALLET_BIN).parent}:{OPENCLAW_HOME / '.openclaw' / 'bin'}"


def wallet_path_prefix() -> str:
    return f"{PROJECT_DIR}:{Path(WALLET_BIN).parent}"
