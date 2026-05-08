#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from urllib.error import URLError


APP_TITLE = "AWP Predict Fleet"
SCRIPT_DIR = Path(__file__).resolve().parent
FLEET_SCRIPT = SCRIPT_DIR / "awp_predict_fleet.py"


def runtime_base_dir() -> Path:
    for parent in [SCRIPT_DIR, *SCRIPT_DIR.parents]:
        if parent.suffix == ".app":
            return parent.parent
    return SCRIPT_DIR


APP_BASE_DIR = runtime_base_dir()
APP_DATA_DIR = APP_BASE_DIR / "awp-predict-data"
FLEET_FILE = APP_DATA_DIR / "fleet.json"
WALLET_BASE_DIR = APP_DATA_DIR / "wallets"
ENV_DIR = APP_DATA_DIR / "env"
LOG_FILE = APP_DATA_DIR / "app.log"
AWP_CHAIN_CACHE_FILE = APP_DATA_DIR / "awp-chain-cache.json"
APP_LOCK_FILE = APP_DATA_DIR / "browser-app-lock.json"
RUNTIME_DIR = APP_DATA_DIR / "runtime"
RUNTIME_BIN_DIR = RUNTIME_DIR / "bin"
RUNTIME_NODE_DIR = RUNTIME_DIR / "node"
RUNTIME_AWP_WALLET_DIR = RUNTIME_DIR / "awp-wallet"
RUNTIME_AWP_WALLET_BIN = RUNTIME_BIN_DIR / "awp-wallet"
RUNTIME_PREDICT_AGENT_BIN = RUNTIME_BIN_DIR / "predict-agent"

BUNDLED_SKILL_DIR = SCRIPT_DIR / "vendor" / "awp-skill"
USER_SKILL_DIR = Path.home() / ".codex" / "skills" / "awp-skill"
BUNDLED_AWP_WALLET_DIR = SCRIPT_DIR / "vendor" / "awp-wallet"

HOST = "127.0.0.1"
START_PORT = 8765


STATE: dict[str, object] = {
    "title": APP_TITLE,
    "step": "启动中...",
    "ready": False,
    "error": "",
    "logs": [],
    "last_result": None,
    "server_url": "",
}
STATE_LOCK = threading.Lock()
SNAPSHOT_CACHE: dict[str, object] = {"ts": 0.0, "data": {"wallets": {"wallets": []}, "profiles": {}}}
SNAPSHOT_TTL_SECONDS = 15


def set_state(**kwargs):
    with STATE_LOCK:
        STATE.update(kwargs)


def append_log(line: str, *, wallet: str = "system"):
    stamped = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][{wallet}] {line}"
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fp:
        fp.write(stamped.rstrip("\n") + "\n")
    with STATE_LOCK:
        logs = load_recent_logs()
        logs.append(stamped)
        STATE["logs"] = logs[-200:]


def load_recent_logs(limit: int = 200) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-limit:]


def load_all_logs() -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()]


def load_awp_chain_cache() -> dict:
    if not AWP_CHAIN_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(AWP_CHAIN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_awp_chain_cache(data: dict):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    AWP_CHAIN_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_awp_truth(current: dict, cached: dict) -> dict:
    if not isinstance(cached, dict):
        return current
    merged = dict(current)
    if merged.get("registered") is None and cached.get("registered") is not None:
        merged["registered"] = cached.get("registered")
    cur_balance = merged.get("balance") if isinstance(merged.get("balance"), dict) else {}
    cached_balance = cached.get("balance") if isinstance(cached.get("balance"), dict) else {}
    if cur_balance:
        for key in ["totalStaked", "totalAllocated", "unallocated", "totalStaked_wei", "totalAllocated_wei", "unallocated_wei"]:
            if (cur_balance.get(key) in (None, "", "0", "0.0", "0.0000")) and cached_balance.get(key):
                cur_balance[key] = cached_balance.get(key)
    elif cached_balance:
        merged["balance"] = dict(cached_balance)
    if not merged.get("allocations") and cached.get("allocations"):
        merged["allocations"] = cached.get("allocations")
    if merged.get("predictStake") in (None, "", "0", "0.0", "0.0000") and cached.get("predictStake"):
        merged["predictStake"] = cached.get("predictStake")
    if merged.get("predictStake_wei") in (None, "", "0") and cached.get("predictStake_wei"):
        merged["predictStake_wei"] = cached.get("predictStake_wei")
    return merged


def is_good_awp_truth(payload: dict) -> bool:
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    balance = payload.get("balance") if isinstance(payload.get("balance"), dict) else {}
    allocations = payload.get("allocations") if isinstance(payload.get("allocations"), list) else []
    if balance.get("totalStaked_wei") not in (None, "", "0"):
        return True
    if balance.get("totalAllocated_wei") not in (None, "", "0"):
        return True
    if allocations:
        return True
    return payload.get("registered") in (True, False)


def extract_action_summaries(payload: object) -> list[tuple[str, str, str, str]]:
    summaries: list[tuple[str, str, str, str]] = []

    def visit(obj: object):
        if isinstance(obj, dict):
            instance = str(obj.get("instance") or "")
            action = str(obj.get("action") or "")
            tx_hash = str(obj.get("txHash") or "")
            status = str(obj.get("status") or "")
            if instance and action:
                summaries.append((instance, action, tx_hash, status))
            result = obj.get("result")
            if result is not None:
                visit(result)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(payload)
    return summaries


def log_step(message: str, *, wallet: str = "system"):
    append_log(f"[STEP] {message}", wallet=wallet)


def log_info(message: str, *, wallet: str = "system"):
    append_log(f"[INFO] {message}", wallet=wallet)


def log_success(message: str, *, wallet: str = "system"):
    append_log(f"[SUCCESS] {message}", wallet=wallet)


def log_error(message: str, *, wallet: str = "system"):
    append_log(f"[ERROR] {message}", wallet=wallet)


def filter_logs(lines: list[str], wallet_filter: str = "", error_only: bool = False) -> list[str]:
    out: list[str] = []
    for line in lines:
        wallet = ""
        level = ""
        m = line.match if False else None
        parts = line.split("]", 3)
        if len(parts) >= 3:
            wallet = parts[1].lstrip("[")
            level = parts[2].strip().lstrip("[")
        if wallet_filter and wallet != wallet_filter:
            continue
        if error_only and level != "ERROR":
            continue
        out.append(line)
    return out


def parse_log_query(query: dict[str, list[str]]) -> tuple[str, bool, int, int]:
    wallet = query.get("wallet", [""])[0].strip()
    error_only = query.get("error_only", ["0"])[0] == "1"
    try:
        page = max(1, int(query.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(200, int(query.get("page_size", ["50"])[0])))
    except ValueError:
        page_size = 50
    return wallet, error_only, page, page_size


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "awp-predict-fleet"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "awp-predict-fleet"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def common_args() -> list[str]:
    return [
        "--project-dir",
        str(SCRIPT_DIR),
        "--fleet-file",
        str(FLEET_FILE),
        "--wallet-base-dir",
        str(WALLET_BASE_DIR),
        "--env-dir",
        str(ENV_DIR),
        "--wallet-bin",
        str(RUNTIME_AWP_WALLET_BIN),
        "--skill-scripts-dir",
        str(BUNDLED_SKILL_DIR / "scripts"),
    ]


def fleet_call(args: list[str]):
    cmd = ["python3", str(FLEET_SCRIPT), *args, *common_args()]
    env = os.environ.copy()
    env["PATH"] = f"{RUNTIME_BIN_DIR}:{env.get('PATH', '')}"
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    text = proc.stdout.strip()
    return json.loads(text) if text else {}


def ensure_bundled_skill_installed():
    if USER_SKILL_DIR.exists() or not BUNDLED_SKILL_DIR.exists():
        return
    set_state(step="正在安装 awp-skill...")
    log_step("正在安装 awp-skill")
    USER_SKILL_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL_DIR, USER_SKILL_DIR, dirs_exist_ok=True)
    log_success(f"Installed bundled awp-skill to {USER_SKILL_DIR}")


def ensure_predict_agent_installed():
    if RUNTIME_PREDICT_AGENT_BIN.exists():
        return
    set_state(step="正在准备 predict-agent...")
    log_step("正在准备 predict-agent")
    arch = "aarch64" if os.uname().machine in {"arm64", "aarch64"} else "x86_64"
    binary = f"predict-agent-darwin-{arch}"
    # Avoid GitHub API rate limits by downloading from the stable latest-release redirect.
    url = f"https://github.com/awp-worknet/prediction-skill/releases/latest/download/{binary}"
    RUNTIME_BIN_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_PREDICT_AGENT_BIN.write_bytes(fetch_bytes(url))
    RUNTIME_PREDICT_AGENT_BIN.chmod(0o755)
    log_success(f"Installed predict-agent to {RUNTIME_PREDICT_AGENT_BIN}")


def ensure_node_runtime_installed():
    node_bin = RUNTIME_NODE_DIR / "bin" / "node"
    if node_bin.exists():
        return
    set_state(step="正在准备 node runtime...")
    log_step("正在准备 node runtime")
    index = fetch_json("https://nodejs.org/dist/index.json")
    if not isinstance(index, list):
        raise RuntimeError("无法获取 Node.js 版本列表")
    match = next((item for item in index if str(item.get("version", "")).startswith("v22.")), None)
    if not match:
        raise RuntimeError("找不到可用的 Node 22 版本")
    version = match["version"]
    arch = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "x64"
    url = f"https://nodejs.org/dist/{version}/node-{version}-darwin-{arch}.tar.gz"
    data = fetch_bytes(url)
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tf:
        members = tf.getmembers()
        top = members[0].name.split("/", 1)[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            tf.extractall(tmpdir)
            extracted = Path(tmpdir) / top
            if RUNTIME_NODE_DIR.exists():
                shutil.rmtree(RUNTIME_NODE_DIR)
            shutil.copytree(extracted, RUNTIME_NODE_DIR)
    log_success(f"Installed node runtime to {RUNTIME_NODE_DIR}")


def ensure_awp_wallet_installed():
    if RUNTIME_AWP_WALLET_BIN.exists():
        return
    if not BUNDLED_AWP_WALLET_DIR.exists():
        raise RuntimeError("包内缺少 awp-wallet 源码")
    set_state(step="正在准备 awp-wallet...")
    log_step("正在准备 awp-wallet")
    if RUNTIME_AWP_WALLET_DIR.exists():
        shutil.rmtree(RUNTIME_AWP_WALLET_DIR)
    shutil.copytree(
        BUNDLED_AWP_WALLET_DIR,
        RUNTIME_AWP_WALLET_DIR,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"),
    )
    env = os.environ.copy()
    env["PATH"] = f"{RUNTIME_NODE_DIR / 'bin'}:{env.get('PATH', '')}"
    npm_cli = RUNTIME_NODE_DIR / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    node_bin = RUNTIME_NODE_DIR / "bin" / "node"
    if not npm_cli.exists():
        raise RuntimeError(f"npm cli not found: {npm_cli}")
    completed = subprocess.run(
        [str(node_bin), str(npm_cli), "install", "--no-audit", "--no-fund"],
        cwd=str(RUNTIME_AWP_WALLET_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "awp-wallet npm install failed")
    wrapper = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"
exec \"$ROOT_DIR/node/bin/node\" \"$ROOT_DIR/awp-wallet/scripts/wallet-cli.js\" \"$@\"
"""
    RUNTIME_AWP_WALLET_BIN.write_text(wrapper, encoding="utf-8")
    RUNTIME_AWP_WALLET_BIN.chmod(0o755)
    log_success(f"Installed awp-wallet runtime to {RUNTIME_AWP_WALLET_DIR}")


def ensure_dependencies():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    WALLET_BASE_DIR.mkdir(parents=True, exist_ok=True)
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_BIN_DIR.mkdir(parents=True, exist_ok=True)
    ensure_bundled_skill_installed()
    ensure_predict_agent_installed()
    ensure_node_runtime_installed()
    ensure_awp_wallet_installed()


def bootstrap():
    try:
        set_state(logs=load_recent_logs())
        set_state(step="正在检查本地环境...", error="")
        log_step("正在检查本地环境")
        ensure_dependencies()
        set_state(step="正在检查默认钱包...")
        log_step("正在检查默认钱包")
        if not FLEET_FILE.exists():
            result = fleet_call(["open"])
            set_state(last_result=result)
        else:
            wallets = fleet_call(["wallet-list"])
            if not wallets.get("wallets"):
                result = fleet_call(["open"])
                set_state(last_result=result)
            else:
                set_state(last_result=wallets)
        log_success("应用已就绪")
        set_state(step="已就绪", ready=True)
    except Exception as exc:  # noqa: BLE001
        log_error(str(exc))
        set_state(step="初始化失败", error=str(exc), ready=False)


def run_action(label: str, args: list[str]):
    def worker():
        try:
            invalidate_snapshot()
            set_state(step=f"正在执行：{label}...", error="")
            log_step(f"正在执行：{label}")
            result = fleet_call(args)
            for instance, action, tx_hash, status in extract_action_summaries(result):
                if tx_hash:
                    log_info(f"{action} txHash={tx_hash} status={status or '-'}", wallet=instance)
            ok = True
            if isinstance(result, list):
                oks = [bool(item.get("ok")) for item in result if isinstance(item, dict) and "ok" in item]
                if oks:
                    ok = all(oks)
            elif isinstance(result, dict) and "ok" in result:
                ok = bool(result.get("ok"))
            if ok:
                log_success(f"已完成：{label}")
                step_text = f"已完成：{label}"
                err_text = ""
            else:
                log_error(f"{label}: 返回结果未成功")
                step_text = f"执行失败：{label}"
                err_text = f"{label} 未成功完成"
            invalidate_snapshot()
            delayed_refresh(2.0)
            delayed_refresh(8.0)
            set_state(last_result=result, step=step_text, error=err_text, ready=ok)
        except Exception as exc:  # noqa: BLE001
            log_error(f"{label}: {exc}")
            invalidate_snapshot()
            set_state(step=f"执行失败：{label}", error=str(exc))

    threading.Thread(target=worker, daemon=True).start()


def run_wallet_batch(label: str, names: list[str], builder):
    def worker():
        try:
            invalidate_snapshot()
            set_state(step=f"正在执行：{label}...", error="")
            log_step(f"正在执行：{label}")
            results = []
            for name in names:
                try:
                    log_info(f"开始：{label}", wallet=name)
                    result = fleet_call(builder(name))
                    for instance, action, tx_hash, status in extract_action_summaries(result):
                        if tx_hash:
                            log_info(f"{action} txHash={tx_hash} status={status or '-'}", wallet=instance or name)
                    item_ok = True
                    item_status = ""
                    if isinstance(result, list):
                        oks = [bool(x.get("ok")) for x in result if isinstance(x, dict) and "ok" in x]
                        if oks:
                            item_ok = all(oks)
                        statuses = [str(x.get("status") or "") for x in result if isinstance(x, dict)]
                        if statuses:
                            item_status = statuses[-1]
                    elif isinstance(result, dict) and "ok" in result:
                        item_ok = bool(result.get("ok"))
                        item_status = str(result.get("status") or "")
                    if item_ok:
                        if item_status == "submitted":
                            log_info(f"完成：{label}（已提交，待确认）", wallet=name)
                        else:
                            log_success(f"完成：{label}", wallet=name)
                    else:
                        log_error(f"{label} 未成功完成", wallet=name)
                    results.append({"instance": name, "ok": item_ok, "result": result})
                except Exception as exc:  # noqa: BLE001
                    log_error(str(exc), wallet=name)
                    results.append({"instance": name, "ok": False, "error": str(exc)})
            all_ok = all(bool(item.get("ok")) for item in results) if results else False
            if all_ok:
                log_success(f"已完成：{label}")
                step_text = f"已完成：{label}"
                err_text = ""
            else:
                log_error(f"{label}: 存在失败项")
                step_text = f"执行失败：{label}"
                err_text = f"{label} 存在失败项"
            invalidate_snapshot()
            delayed_refresh(2.0)
            delayed_refresh(8.0)
            set_state(last_result=results, step=step_text, error=err_text, ready=all_ok)
        except Exception as exc:  # noqa: BLE001
            log_error(str(exc))
            invalidate_snapshot()
            set_state(step=f"执行失败：{label}", error=str(exc))

    threading.Thread(target=worker, daemon=True).start()


def fleet_snapshot() -> dict:
    now = time.time()
    awp_cache = load_awp_chain_cache()
    try:
        wallets = fleet_call(["wallet-list"])
    except Exception as exc:  # noqa: BLE001
        wallets = {"error": str(exc), "wallets": []}
    try:
        profiles = fleet_call(["profile-list"])
    except Exception as exc:  # noqa: BLE001
        profiles = {"error": str(exc)}
    try:
        health = fleet_call(["health", "--all"])
    except Exception:
        health = []
    try:
        history = fleet_call(["history", "--all", "--limit", "1"])
    except Exception:
        history = []
    try:
        awp_status = fleet_call(["awp-status", "--all"])
    except Exception:
        awp_status = []

    health_by = {item.get("instance"): item for item in health if isinstance(item, dict)} if isinstance(health, list) else {}
    history_by = {item.get("instance"): item for item in history if isinstance(item, dict)} if isinstance(history, list) else {}
    awp_by = {item.get("instance"): item for item in awp_status if isinstance(item, dict)} if isinstance(awp_status, list) else {}
    wallet_rows = []
    for row in (wallets.get("wallets") or []):
        if not isinstance(row, dict):
            continue
        instance = row.get("instance")
        health_row = health_by.get(instance, {})
        history_row = history_by.get(instance, {})
        awp_row = awp_by.get(instance, {})
        awp_payload = awp_row.get("status") if isinstance(awp_row, dict) else {}
        cache_key = str(row.get("address") or instance or "")
        cached_truth = awp_cache.get(cache_key, {})
        if isinstance(awp_payload, dict):
            if is_good_awp_truth(awp_payload):
                awp_cache[cache_key] = awp_payload
            else:
                awp_payload = merge_awp_truth(awp_payload, cached_truth)
        elif cached_truth:
            awp_payload = cached_truth
        awp_balance = (awp_payload or {}).get("balance") or {}
        allocations = (awp_payload or {}).get("allocations") or []
        predictions = history_row.get("predictions") or []
        latest_prediction_at = ""
        for item in predictions:
            if not isinstance(item, dict):
                continue
            if item.get("order_status") in {"open", "filled", "cancelled"}:
                latest_prediction_at = item.get("created_at", "") or ""
                if latest_prediction_at:
                    break
        row = dict(row)
        row["active"] = "active" if health_row.get("normal") else "inactive"
        row["loop_status"] = health_row.get("loop", "-")
        row["monitor_status"] = health_row.get("monitor", "-")
        row["balance"] = health_row.get("balance")
        row["recent_error"] = health_row.get("error", "")
        row["recent_prediction_at"] = latest_prediction_at
        submitted = health_row.get("submissions_used")
        slot_limit = health_row.get("slot_limit")
        row["daily_usage"] = f"{submitted or 0}/{slot_limit or 0}"
        inferred_registered = True if (history_row.get("count") or 0) > 0 or health_row.get("normal") else None
        row["awp_registered"] = (awp_payload or {}).get("registered")
        if row["awp_registered"] is None:
            row["awp_registered"] = inferred_registered
        row["awp_staked"] = awp_balance.get("totalStaked") if awp_balance else "-"
        row["awp_allocated"] = awp_balance.get("totalAllocated") if awp_balance else "-"
        predict_stake = str((awp_payload or {}).get("predictStake") or "0")
        row["awp_in_predict"] = any(
            isinstance(item, dict) and str(item.get("worknetId")) == "845300000003"
            for item in allocations
        ) or (predict_stake not in {"0", "0.0", "0.0000", ""})
        if not row["awp_in_predict"] and (history_row.get("count") or 0) > 0:
            row["awp_in_predict"] = True
        ready_reasons = []
        if row["loop_status"] != "active":
            ready_reasons.append("loop inactive")
        if row["monitor_status"] != "active":
            ready_reasons.append("monitor inactive")
        if row["awp_registered"] is False:
            ready_reasons.append("未注册 AWP")
        if not row["awp_in_predict"]:
            ready_reasons.append("未分配到 Predict")
        row["predict_ready"] = len(ready_reasons) == 0
        row["predict_ready_reason"] = "可预测" if not ready_reasons else " / ".join(ready_reasons)
        wallet_rows.append(row)
    save_awp_chain_cache(awp_cache)
    data = {"wallets": {"wallets": wallet_rows}, "profiles": profiles}
    SNAPSHOT_CACHE["ts"] = now
    SNAPSHOT_CACHE["data"] = data
    return data


def refresh_snapshot_cache(force: bool = False):
    if not force and time.time() - float(SNAPSHOT_CACHE.get("ts", 0.0)) < SNAPSHOT_TTL_SECONDS:
        return
    data = fleet_snapshot()
    SNAPSHOT_CACHE["data"] = data
    SNAPSHOT_CACHE["ts"] = time.time()


def invalidate_snapshot():
    SNAPSHOT_CACHE["ts"] = 0.0


def snapshot_refresher():
    while True:
        try:
            refresh_snapshot_cache(force=True)
        except Exception as exc:  # noqa: BLE001
            log_error(f"snapshot refresh: {exc}")
        time.sleep(SNAPSHOT_TTL_SECONDS)


def delayed_refresh(delay_seconds: float = 3.0):
    def worker():
        time.sleep(delay_seconds)
        try:
            refresh_snapshot_cache(force=True)
        except Exception as exc:  # noqa: BLE001
            log_error(f"delayed refresh: {exc}")
    threading.Thread(target=worker, daemon=True).start()


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AWP Predict Fleet</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f5f6f8; color: #111; }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
    .hero, .panel { background: white; border-radius: 16px; padding: 18px; box-shadow: 0 10px 30px rgba(0,0,0,.06); margin-bottom: 16px; }
    .status { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
    .muted { color: #666; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap: 16px; }
    button { border: 0; border-radius: 10px; padding: 10px 14px; cursor: pointer; background: #111; color: white; }
    button.secondary { background: #e9edf3; color: #111; }
    pre { white-space: pre-wrap; word-break: break-word; background: #111; color: #d5f5d1; padding: 16px; border-radius: 12px; min-height: 180px; }
    code { background: #f0f2f5; padding: 2px 6px; border-radius: 6px; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .error { color: #b00020; font-weight: 600; }
    input, select { width: 100%; box-sizing: border-box; border: 1px solid #d5d9df; border-radius: 10px; padding: 10px 12px; margin-top: 6px; }
    label { display: block; margin-top: 12px; font-size: 13px; color: #444; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
    .wallet-card { border: 1px solid #eceff4; border-radius: 12px; padding: 10px 12px; margin-top: 10px; background: #fafbfc; }
    .wallet-card strong { display: block; margin-bottom: 4px; }
    .wallet-head { display:flex; align-items:center; gap:10px; }
    .wallet-meta { font-size: 13px; color: #444; line-height: 1.5; margin-top: 6px; }
    .logs { background: #111; color: #e7f5e5; padding: 14px; border-radius: 12px; min-height: 180px; font-family: Menlo, monospace; font-size: 12px; }
    .log-line { padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,.06); }
    .log-tag { display:inline-block; min-width: 74px; font-weight: 700; }
    .log-step .log-tag { color: #9fd3ff; }
    .log-info .log-tag { color: #c7d0dc; }
    .log-success .log-tag { color: #8ff0a4; }
    .log-error .log-tag { color: #ff8b8b; }
    .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; align-items:center; }
    .toolbar input { width: 220px; margin-top: 0; }
    .toolbar label { margin-top: 0; display:flex; align-items:center; gap:6px; }
    .pager { display:flex; gap:8px; align-items:center; margin-top:10px; }
    .group-title { margin-top: 14px; font-size: 13px; color: #555; font-weight: 700; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="status" id="step">启动中...</div>
      <div class="muted">数据目录：<code id="dataDir"></code></div>
      <div class="muted">Fleet 文件：<code id="fleetFile"></code></div>
      <div class="muted">提示：钱包卡片状态仅供参考。真实预测是否成功，请打开对应监控页（例如 `http://127.0.0.1:8791/dashboard`）查看。</div>
      <div class="muted">如果出现分配数量异常或大于预期，建议先去官网取消分配，再回到软件里重新分配。</div>
      <div class="error" id="error"></div>
      <div class="row">
        <button onclick="act('bootstrap')">重新检查依赖</button>
        <button class="secondary" onclick="act('force-refresh')">强制刷新钱包状态</button>
      </div>
    </div>
    <div class="grid">
      <div class="panel">
        <strong>创建新钱包</strong>
        <label>钱包名
          <input id="walletName" value="default" />
        </label>
        <label>Profile
          <input id="walletProfile" value="default" />
        </label>
        <label>Wallet Home
          <input id="walletHome" placeholder="可留空，自动分配" />
        </label>
        <label>Agent ID
          <input id="agentId" placeholder="可留空，自动生成" />
        </label>
        <label>Monitor Port
          <input id="monitorPort" placeholder="可留空，自动分配" />
        </label>
        <label>AWP 数量
          <input id="stakeAmount" value="1000" />
        </label>
        <label>锁定天数
          <input id="lockDays" value="3" />
        </label>
        <label>Worknet
          <input id="worknetId" value="845300000003" />
        </label>
        <div class="actions">
          <button onclick="actJson('open-wallet', walletPayload())">创建/打开钱包</button>
        </div>
      </div>
      <div class="panel">
        <strong>共享 Profile / 选中钱包配置</strong>
        <label>Profile 名
          <input id="profileName" value="default" />
        </label>
        <label>Model
          <input id="profileModel" value="gpt-5.4" />
        </label>
        <label>Base URL
          <input id="profileBaseUrl" />
        </label>
        <label>API Key
          <input id="profileApiKey" />
        </label>
        <label>Predict Server URL
          <input id="profileServerUrl" value="https://api.agentpredict.work" />
        </label>
        <label>Loop Interval
          <input id="profileLoopInterval" value="120" />
        </label>
        <div class="actions">
          <button onclick="actJson('save-profile', profilePayload())">保存共享 Profile</button>
          <button class="secondary" onclick="actJson('wallet-config-selected', walletConfigPayload())">应用到选中钱包</button>
        </div>
      </div>
    </div>
    <div class="panel">
      <strong>钱包列表与批量操作</strong>
      <div class="group-title">选择</div>
      <div class="actions">
        <button onclick="toggleAll(true)">全选</button>
        <button class="secondary" onclick="toggleAll(false)">取消全选</button>
      </div>
      <div class="group-title">钱包管理</div>
      <div class="actions">
        <button class="secondary" onclick="actJson('delete-selected', selectedPayload())">删除选中钱包</button>
        <button class="secondary" onclick="act('force-refresh')">强制刷新钱包状态</button>
      </div>
      <div class="group-title">AWP 操作</div>
      <div class="actions">
        <button class="secondary" onclick="actJson('register-selected', selectedPayload())">注册 AWP</button>
        <button class="secondary" onclick="actJson('stake-selected', stakeSelectedPayload())">质押 AWP</button>
        <button class="secondary" onclick="actJson('allocate-selected', allocateSelectedPayload())">分配到 Predict</button>
        <button class="secondary" onclick="actJson('deallocate-selected', allocateSelectedPayload())">取消分配</button>
        <button class="secondary" onclick="actJson('stake-allocate-selected', stakeAllocateSelectedPayload())">质押并分配</button>
      </div>
      <div class="group-title">预测控制</div>
      <div class="actions">
        <button onclick="actJson('start-selected', selectedPayload())">启动预测</button>
        <button class="secondary" onclick="actJson('stop-selected', selectedPayload())">停止预测</button>
      </div>
      <div id="walletList"></div>
    </div>
    <div class="panel">
      <strong>最近结果</strong>
      <pre id="result"></pre>
    </div>
    <div class="panel">
      <strong>运行日志</strong>
      <div class="toolbar">
        <input id="logWalletFilter" placeholder="按钱包名过滤，如 default / wallet02" />
        <label><input type="checkbox" id="logErrorOnly" /> 只看 ERROR</label>
        <button class="secondary" onclick="exportLogs()">导出日志文件</button>
        <button class="secondary" onclick="refreshLogs(true)">刷新日志</button>
      </div>
      <div id="logs" class="logs"></div>
      <div class="pager">
        <button class="secondary" onclick="prevLogPage()">上一页</button>
        <span id="logPageInfo">1 / 1</span>
        <button class="secondary" onclick="nextLogPage()">下一页</button>
      </div>
    </div>
  </div>
<script>
const selectedWallets = new Set();
let logPage = 1;
let logTotalPages = 1;
let stateLogsCache = [];
let logFilterKey = '';
function val(id) { return document.getElementById(id).value.trim(); }
function currentWallet() { return val('walletName') || 'default'; }
function walletPayload() {
  return {
    name: currentWallet(),
    profile: val('walletProfile') || 'default',
    wallet_home: val('walletHome'),
    agent_id: val('agentId'),
    monitor_port: val('monitorPort')
  };
}
function selectedWalletNames() {
  return Array.from(selectedWallets);
}
function selectedPayload() {
  return { names: selectedWalletNames() };
}
function stakeSelectedPayload() {
  return {
    names: selectedWalletNames(),
    amount: val('stakeAmount') || '1000',
    lock_days: val('lockDays') || '3',
    worknet: val('worknetId') || '845300000003'
  };
}
function allocateSelectedPayload() {
  return {
    names: selectedWalletNames(),
    amount: val('stakeAmount') || '1000',
    worknet: val('worknetId') || '845300000003'
  };
}
function stakeAllocateSelectedPayload() {
  return {
    names: selectedWalletNames(),
    amount: val('stakeAmount') || '1000',
    lock_days: val('lockDays') || '3',
    worknet: val('worknetId') || '845300000003'
  };
}
function stakePayload() {
  return {
    name: currentWallet(),
    amount: val('stakeAmount') || '1000',
    lock_days: val('lockDays') || '90'
  };
}
function profilePayload() {
  return {
    profile: val('profileName') || 'default',
    model: val('profileModel'),
    base_url: val('profileBaseUrl'),
    api_key: val('profileApiKey'),
    predict_server_url: val('profileServerUrl'),
    loop_interval: val('profileLoopInterval')
  };
}
function walletConfigPayload() {
  return {
    names: selectedWalletNames(),
    profile: val('profileName') || 'default',
    model: val('profileModel'),
    base_url: val('profileBaseUrl'),
    api_key: val('profileApiKey'),
    predict_server_url: val('profileServerUrl'),
    loop_interval: val('profileLoopInterval'),
    wallet_home: val('walletHome'),
    agent_id: val('agentId'),
    monitor_port: val('monitorPort')
  };
}
function toggleAll(checked) {
  document.querySelectorAll('input[name="walletSelect"]').forEach(el => {
    el.checked = checked;
    if (checked) selectedWallets.add(el.value);
    else selectedWallets.delete(el.value);
  });
}
function onWalletToggle(checkbox) {
  if (checkbox.checked) selectedWallets.add(checkbox.value);
  else selectedWallets.delete(checkbox.value);
}
async function load() {
  const res = await fetch('/api/state');
  const data = await res.json();
  document.getElementById('step').textContent = data.step || '';
  document.getElementById('error').textContent = data.error || '';
  document.getElementById('dataDir').textContent = data.dataDir || '';
  document.getElementById('fleetFile').textContent = data.fleetFile || '';
  document.getElementById('result').textContent = JSON.stringify(data.last_result || {}, null, 2);
  renderWallets((data.fleet || {}).wallets || {});
  stateLogsCache = data.logs || [];
  await refreshLogs(false);
}
function renderLogs(lines, page, totalPages) {
  const walletFilter = val('logWalletFilter');
  const errorOnly = document.getElementById('logErrorOnly').checked;
  const box = document.getElementById('logs');
  logPage = page || 1;
  logTotalPages = totalPages || 1;
  document.getElementById('logPageInfo').textContent = `${logPage} / ${logTotalPages}`;
  box.innerHTML = lines.map(line => {
    let cls = 'log-info', tag = 'INFO', text = line, ts = '', wallet = 'system';
    const m = String(line).match(/^\\[([^\\]]+)\\]\\[([^\\]]+)\\]\\s+\\[(STEP|INFO|SUCCESS|ERROR)\\]\\s*(.*)$/);
    if (m) {
      ts = m[1];
      wallet = m[2];
      tag = m[3];
      text = m[4];
      cls = 'log-' + tag.toLowerCase();
    }
    return { line, cls, tag, text, ts, wallet };
  }).filter(item => {
    if (walletFilter && item.wallet !== walletFilter) return false;
    if (errorOnly && item.tag !== 'ERROR') return false;
    return true;
  }).map(item =>
    `<div class="log-line ${item.cls}"><span class="log-tag">${item.tag}</span><span style="color:#999;">[${item.ts}]</span> <span style="color:#caa7ff;">[${item.wallet}]</span> <span>${item.text}</span></div>`
  ).join('');
}
function exportLogs() {
  const wallet = encodeURIComponent(val('logWalletFilter'));
  const errorOnly = document.getElementById('logErrorOnly').checked ? '1' : '0';
  window.open(`/api/logs/export?wallet=${wallet}&error_only=${errorOnly}`, '_blank');
}
async function refreshLogs(resetPage = true) {
  const walletRaw = val('logWalletFilter');
  const errorOnlyRaw = document.getElementById('logErrorOnly').checked ? '1' : '0';
  const nextFilterKey = `${walletRaw}|${errorOnlyRaw}`;
  if (resetPage || nextFilterKey !== logFilterKey) {
    logPage = 1;
    logFilterKey = nextFilterKey;
  }
  const wallet = encodeURIComponent(walletRaw);
  const errorOnly = errorOnlyRaw;
  try {
    const res = await fetch(`/api/logs?page=${logPage}&page_size=50&wallet=${wallet}&error_only=${errorOnly}`);
    const data = await res.json();
    const lines = data.lines || [];
    if (lines.length || !stateLogsCache.length) {
      renderLogs(lines, data.page || 1, data.total_pages || 1);
    } else {
      renderLogs(stateLogsCache.slice(-50), logPage, logTotalPages);
    }
  } catch (err) {
    console.error('refreshLogs failed', err);
  }
}
async function prevLogPage() {
  if (logPage <= 1) return;
  logPage -= 1;
  await refreshLogs(false);
}
async function nextLogPage() {
  if (logPage >= logTotalPages) return;
  logPage += 1;
  await refreshLogs(false);
}
function renderWallets(payload) {
  const holder = document.getElementById('walletList');
  const rows = payload.wallets || [];
  if (!rows.length) {
    selectedWallets.clear();
    holder.innerHTML = '<div class="muted" style="margin-top:12px;">当前还没有钱包。</div>';
    return;
  }
  holder.innerHTML = rows.map(row => `
    <div class="wallet-card">
      <div class="wallet-head">
        <input type="checkbox" name="walletSelect" value="${row.instance}" ${selectedWallets.has(row.instance) ? 'checked' : ''} onchange="onWalletToggle(this)" />
        <strong>${row.instance}</strong>
      </div>
      <div class="wallet-meta">
        <div>备注：${row.instance || '-'}</div>
        <div>配置组：${row.profile || '-'}</div>
        <div>地址：${row.address || '未初始化'}${row.instance ? `（${row.instance}）` : ''}</div>
        <div>模型：${row.model || '-'}</div>
        <div>监控端口：${row.monitor_port || '-'}</div>
        <div>运行状态：${row.active || '-'}</div>
        <div>预测循环：${row.loop_status || '-'}</div>
        <div>监控服务：${row.monitor_status || '-'}</div>
        <div>是否可预测：${row.predict_ready ? '是' : '否'}（${row.predict_ready_reason || '-'}）</div>
        <div>余额：${row.balance || '-'}</div>
        <div>是否已注册 AWP：${row.awp_registered === true ? '是' : row.awp_registered === false ? '否' : '-'}</div>
        <div>已质押 AWP：${row.awp_staked || '0.0000'}</div>
        <div>已分配 AWP：${row.awp_allocated || '0.0000'}</div>
        <div>是否已分配到 Predict：${row.awp_in_predict ? '是' : '否'}</div>
        <div>每日次数：${row.daily_usage || '0/0'}</div>
        <div>最近成功提交：${row.recent_prediction_at || '-'}</div>
        <div>最近错误：${row.recent_error || '-'}</div>
      </div>
    </div>
  `).join('');
}
async function act(action) {
  document.getElementById('step').textContent = '请求已发送，正在处理...';
  const res = await fetch('/api/action?action=' + encodeURIComponent(action), {method:'POST'});
  const data = await res.json().catch(() => ({}));
  document.getElementById('error').textContent = (!res.ok && data.error) ? data.error : '';
  setTimeout(load, 300);
}
async function actJson(action, payload) {
  document.getElementById('step').textContent = '请求已发送，正在处理...';
  const res = await fetch('/api/action?action=' + encodeURIComponent(action), {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload || {})
  });
  const data = await res.json().catch(() => ({}));
  document.getElementById('error').textContent = (!res.ok && data.error) ? data.error : '';
  setTimeout(load, 300);
}
setInterval(load, 3000);
document.getElementById('logWalletFilter').addEventListener('input', () => refreshLogs());
document.getElementById('logErrorOnly').addEventListener('change', () => refreshLogs());
setTimeout(() => act('force-refresh'), 500);
load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return

    def send_json(self, status: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            return self.send_html(HTML)
        if parsed.path == "/api/state":
            with STATE_LOCK:
                payload = dict(STATE)
            payload["logs"] = load_recent_logs(50)
            payload["dataDir"] = str(APP_DATA_DIR)
            payload["fleetFile"] = str(FLEET_FILE)
            try:
                payload["fleet"] = fleet_snapshot()
            except Exception as exc:  # noqa: BLE001
                log_error(f"api/state fleet_snapshot: {exc}")
                payload["fleet"] = {"wallets": {"wallets": []}, "profiles": {}}
            return self.send_json(200, payload)
        if parsed.path == "/api/logs":
            wallet, error_only, page, page_size = parse_log_query(query)
            lines = filter_logs(load_all_logs(), wallet, error_only)
            total = len(lines)
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            start = max(0, total - page * page_size)
            end = total - (page - 1) * page_size
            return self.send_json(200, {
                "lines": lines[start:end],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            })
        if parsed.path == "/api/logs/export":
            wallet, error_only, _page, _page_size = parse_log_query(query)
            body = ("\n".join(filter_logs(load_all_logs(), wallet, error_only)) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="awp-predict-log.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/action":
            return self.send_json(404, {"error": "not found"})
        action = parse_qs(parsed.query).get("action", [""])[0]
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            body = {}
        if action == "bootstrap":
            threading.Thread(target=bootstrap, daemon=True).start()
        elif action == "force-refresh":
            def worker():
                try:
                    SNAPSHOT_CACHE["ts"] = 0.0
                    set_state(step="正在强制刷新钱包状态...", error="")
                    refresh_snapshot_cache(force=True)
                    set_state(step="钱包状态已刷新", error="")
                except Exception as exc:  # noqa: BLE001
                    log_error(f"force-refresh: {exc}")
                    set_state(step="钱包状态刷新失败", error=str(exc))
            threading.Thread(target=worker, daemon=True).start()
        elif action == "open-default":
            run_action("创建/打开默认钱包", ["open"])
        elif action == "start-default":
            run_action("启动默认预测", ["start", "default"])
        elif action == "status-default":
            run_action("默认钱包状态", ["status", "default"])
        elif action == "health-all":
            run_action("检查全部健康", ["health", "--all"])
        elif action == "wallet-list":
            run_action("钱包列表", ["wallet-list"])
        elif action == "profile-list":
            run_action("Profile 列表", ["profile-list"])
        elif action == "open-wallet":
            args = ["open", body.get("name", "default")]
            if body.get("profile"):
                args += ["--profile", str(body["profile"])]
            if body.get("wallet_home"):
                args += ["--wallet-home", str(body["wallet_home"])]
            if body.get("agent_id"):
                args += ["--agent-id", str(body["agent_id"])]
            if body.get("monitor_port"):
                args += ["--monitor-port", str(body["monitor_port"])]
            run_action("创建/打开钱包", args)
        elif action == "save-profile":
            args = ["profile-set", body.get("profile", "default")]
            for key, flag in [
                ("model", "--model"),
                ("base_url", "--base-url"),
                ("api_key", "--api-key"),
                ("predict_server_url", "--predict-server-url"),
                ("loop_interval", "--loop-interval"),
            ]:
                if body.get(key):
                    args += [flag, str(body[key])]
            run_action("保存共享 Profile", args)
        elif action == "wallet-config":
            args = ["wallet-config", body.get("name", "default")]
            for key, flag in [
                ("profile", "--profile"),
                ("wallet_home", "--wallet-home"),
                ("agent_id", "--agent-id"),
                ("monitor_port", "--monitor-port"),
                ("model", "--model"),
                ("base_url", "--base-url"),
                ("api_key", "--api-key"),
                ("predict_server_url", "--predict-server-url"),
                ("loop_interval", "--loop-interval"),
            ]:
                if body.get(key):
                    args += [flag, str(body[key])]
            run_action("应用钱包配置", args)
        elif action == "wallet-config-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            def builder(name):
                args = ["wallet-config", name]
                for key, flag in [
                    ("profile", "--profile"),
                    ("wallet_home", "--wallet-home"),
                    ("agent_id", "--agent-id"),
                    ("monitor_port", "--monitor-port"),
                    ("model", "--model"),
                    ("base_url", "--base-url"),
                    ("api_key", "--api-key"),
                    ("predict_server_url", "--predict-server-url"),
                    ("loop_interval", "--loop-interval"),
                ]:
                    if body.get(key):
                        args += [flag, str(body[key])]
                return args
            run_wallet_batch("批量应用钱包配置", names, builder)
        elif action == "start-wallet":
            run_action("启动单钱包预测", ["start", body.get("name", "default")])
        elif action == "stop-wallet":
            run_action("停止单钱包预测", ["stop", body.get("name", "default")])
        elif action == "status-wallet":
            run_action("单钱包状态", ["status", body.get("name", "default")])
        elif action == "balance-wallet":
            run_action("单钱包余额", ["balance", body.get("name", "default")])
        elif action == "history-wallet":
            run_action("单钱包历史", ["history", body.get("name", "default"), "--limit", "5"])
        elif action == "health-wallet":
            run_action("单钱包健康", ["health", body.get("name", "default")])
        elif action == "stake-wallet":
            run_action(
                "单钱包质押",
                [
                    "stake",
                    body.get("name", "default"),
                    "--amount",
                    str(body.get("amount", "1000")),
                    "--lock-days",
                    str(body.get("lock_days", "3")),
                ],
            )
        elif action == "start-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量启动预测", names, lambda name: ["start", name])
        elif action == "delete-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量删除钱包", names, lambda name: ["delete", name])
        elif action == "register-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量注册 AWP", names, lambda name: ["register", name])
        elif action == "stop-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量停止预测", names, lambda name: ["stop", name])
        elif action == "status-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量查询状态", names, lambda name: ["status", name])
        elif action == "balance-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量查询余额", names, lambda name: ["balance", name])
        elif action == "history-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量查询历史", names, lambda name: ["history", name, "--limit", "5"])
        elif action == "health-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch("批量健康检查", names, lambda name: ["health", name])
        elif action == "stake-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch(
                "批量质押 AWP",
                names,
                lambda name: [
                    "stake",
                    name,
                    "--amount",
                    str(body.get("amount", "1000")),
                    "--lock-days",
                    str(body.get("lock_days", "3")),
                ],
            )
        elif action == "allocate-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch(
                "批量分配到 Predict",
                names,
                lambda name: [
                    "allocate",
                    name,
                    "--amount",
                    str(body.get("amount", "1000")),
                    "--worknet",
                    str(body.get("worknet", "845300000003")),
                ],
            )
        elif action == "deallocate-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch(
                "批量取消分配",
                names,
                lambda name: [
                    "deallocate",
                    name,
                    "--amount",
                    str(body.get("amount", "1000")),
                    "--worknet",
                    str(body.get("worknet", "845300000003")),
                ],
            )
        elif action == "stake-allocate-selected":
            names = body.get("names") or []
            if not names:
                return self.send_json(400, {"error": "no wallets selected"})
            run_wallet_batch(
                "批量质押并分配",
                names,
                lambda name: [
                    "stake-allocate",
                    name,
                    "--amount",
                    str(body.get("amount", "1000")),
                    "--lock-days",
                    str(body.get("lock_days", "90")),
                    "--worknet",
                    str(body.get("worknet", "845300000003")),
                ],
            )
        elif action == "start-all":
            run_action("启动全部钱包预测", ["start", "--all"])
        else:
            return self.send_json(400, {"error": "unknown action"})
        return self.send_json(200, {"ok": True, "action": action})


def pick_port() -> int:
    port = START_PORT
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                port += 1


def wait_until_server_ready(url: str, timeout_seconds: int = 8) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_lock() -> dict:
    if not APP_LOCK_FILE.exists():
        return {}
    try:
        data = json.loads(APP_LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_lock(pid: int, port: int):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    APP_LOCK_FILE.write_text(
        json.dumps({"pid": pid, "port": port, "url": f"http://{HOST}:{port}/"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_lock():
    try:
        APP_LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def reuse_existing_instance() -> bool:
    info = read_lock()
    pid = int(info.get("pid") or 0)
    port = int(info.get("port") or 0)
    url = str(info.get("url") or "")
    if not pid or not port or not url:
        return False
    if not process_alive(pid):
        clear_lock()
        return False
    if wait_until_server_ready(url, timeout_seconds=1):
        open_browser_with_retry(url, attempts=2)
        return True
    clear_lock()
    return False


def open_browser_with_retry(url: str, attempts: int = 4):
    for idx in range(attempts):
        try:
            ok = webbrowser.open(url)
            if ok:
                log_info(f"已打开浏览器：{url}")
                return
        except Exception as exc:  # noqa: BLE001
            log_error(f"打开浏览器失败: {exc}")
        time.sleep(1 + idx)
    log_error(f"自动打开浏览器失败，请手动访问：{url}")


def main():
    if reuse_existing_instance():
        return
    port = pick_port()
    url = f"http://{HOST}:{port}/"
    set_state(server_url=url)
    log_info(f"本地控制台地址：{url}")
    server = ThreadingHTTPServer((HOST, port), Handler)
    write_lock(os.getpid(), port)
    threading.Thread(target=snapshot_refresher, daemon=True).start()
    threading.Thread(target=bootstrap, daemon=True).start()
    threading.Thread(
        target=lambda: open_browser_with_retry(url) if wait_until_server_ready(url) else log_error(f"本地服务未就绪：{url}"),
        daemon=True,
    ).start()
    try:
        server.serve_forever()
    finally:
        lock = read_lock()
        if int(lock.get("pid") or 0) == os.getpid():
            clear_lock()


if __name__ == "__main__":
    main()
