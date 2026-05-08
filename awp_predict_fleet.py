#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import awp_predict_manager as manager


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FLEET_FILE = SCRIPT_DIR / "fleet.json"


def die(message: str, code: int = 1) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def load_fleet(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"profiles": {"default": {}}, "wallets": {}}
    data = manager.load_json_file(path)
    data.setdefault("profiles", {"default": {}})
    data.setdefault("wallets", {})
    if "default" not in data["profiles"]:
        data["profiles"]["default"] = {}
    return data


def save_fleet(path: Path, data: dict[str, Any]) -> None:
    manager.save_json_file(path, data)


def fleet_cfg(args: argparse.Namespace) -> tuple[manager.ManagerConfig, Path]:
    cfg = manager.build_config(args)
    fleet_file = Path(getattr(args, "fleet_file", DEFAULT_FLEET_FILE)).expanduser()
    return cfg, fleet_file


def deep_copy_dict(obj: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(obj))


def merge_dict(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = deep_copy_dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def default_profile(cfg: manager.ManagerConfig) -> dict[str, Any]:
    return {
        "predict_server_url": cfg.predict_server_url,
        "openai": {
            "model": cfg.openai_model,
            "base_url": cfg.openai_base_url,
            "api_key": "",
        },
        "loop_interval": cfg.loop_interval,
    }


def resolve_wallet_settings(name: str, wallet: dict[str, Any], fleet: dict[str, Any], cfg: manager.ManagerConfig) -> dict[str, Any]:
    result = default_profile(cfg)
    profile_name = wallet.get("profile") or "default"
    profile = fleet.get("profiles", {}).get(profile_name, {})
    if isinstance(profile, dict):
        result = merge_dict(result, profile)
    overrides = wallet.get("overrides", {})
    if isinstance(overrides, dict):
        result = merge_dict(result, overrides)
    result["wallet_home"] = wallet.get("wallet_home") or str(manager.wallet_home_for(name, cfg))
    result["agent_id"] = wallet.get("agent_id") or manager.default_agent_id(name)
    result["monitor"] = merge_dict(
        {
            "host": cfg.monitor_host,
            "port": wallet.get("monitor_port") or manager.next_monitor_port(cfg),
        },
        result.get("monitor", {}) if isinstance(result.get("monitor"), dict) else {},
    )
    result["profile"] = profile_name
    return result


def wallet_entry(name: str, cfg: manager.ManagerConfig, wallet_home: str | None = None, agent_id: str | None = None, profile: str | None = None, monitor_port: int | None = None) -> dict[str, Any]:
    return {
        "profile": profile or "default",
        "wallet_home": wallet_home or str(manager.wallet_home_for(name, cfg)),
        "agent_id": agent_id or manager.default_agent_id(name),
        "monitor_port": monitor_port or manager.next_monitor_port(cfg),
        "overrides": {},
    }


def recover_address_from_wallet_dir(name: str, cfg: manager.ManagerConfig) -> str:
    wallet_dir = cfg.wallet_base_dir / name
    candidates = [
        wallet_dir / ".openclaw-wallet" / "wallets" / f"awp-{name}" / "wallet.json",
        wallet_dir / ".openclaw-wallet" / "wallets" / wallet_dir.name / "wallet.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("address"):
            return str(data["address"])
    return ""


def ensure_wallet_registered(name: str, fleet: dict[str, Any], cfg: manager.ManagerConfig, *, wallet_home: str | None = None, agent_id: str | None = None, profile: str | None = None, monitor_port: int | None = None) -> dict[str, Any]:
    wallets = fleet.setdefault("wallets", {})
    if name not in wallets:
        wallets[name] = wallet_entry(name, cfg, wallet_home=wallet_home, agent_id=agent_id, profile=profile, monitor_port=monitor_port)
    else:
        if wallet_home:
            wallets[name]["wallet_home"] = wallet_home
        if agent_id:
            wallets[name]["agent_id"] = agent_id
        if profile:
            wallets[name]["profile"] = profile
        if monitor_port:
            wallets[name]["monitor_port"] = monitor_port
    return wallets[name]


def validate_assignment_port(name: str, port: int, fleet: dict[str, Any]) -> None:
    for other_name, other_wallet in fleet.get("wallets", {}).items():
        if other_name == name:
            continue
        if int(other_wallet.get("monitor_port") or 0) == int(port):
            die(f"monitor port already assigned to wallet {other_name}: {port}")


def sync_wallet_instance(name: str, wallet: dict[str, Any], fleet: dict[str, Any], cfg: manager.ManagerConfig) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    settings = resolve_wallet_settings(name, wallet, fleet, cfg)
    monitor_port = int(settings["monitor"]["port"])
    validate_assignment_port(name, monitor_port, fleet)
    profile = {
        "instance": name,
        "wallet_home": settings["wallet_home"],
        "agent_id": settings["agent_id"],
        "predict_server_url": settings["predict_server_url"],
        "openai": settings.get("openai", {}),
        "monitor": settings.get("monitor", {}),
        "loop_interval": settings.get("loop_interval", cfg.loop_interval),
        "services": {
            "gateway": manager.DEFAULT_GATEWAY_SERVICE,
            "loop": f"awp-predict-loop@{name}",
            "monitor": f"awp-predict-monitor@{name}",
            "journal_loop": f"awp-predict-loop@{name}",
        },
    }
    env_data = manager.sync_instance_files(name, cfg, profile)
    wallet_info = manager.ensure_wallet(name, env_data, cfg)
    wallet["address"] = wallet_info["address"]
    wallet["wallet_home"] = settings["wallet_home"]
    wallet["agent_id"] = settings["agent_id"]
    wallet["monitor_port"] = monitor_port
    return profile, env_data, wallet_info


def predict_env_for(name: str, wallet: dict[str, Any], fleet: dict[str, Any], cfg: manager.ManagerConfig) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    profile, env_data, wallet_info = sync_wallet_instance(name, wallet, fleet, cfg)
    env = manager.wallet_env(env_data["AWP_AGENT_ID"], Path(env_data["AWP_WALLET_HOME"]), cfg.wallet_bin, cfg.project_dir)
    env["PATH"] = f"{cfg.project_dir}:{cfg.wallet_bin.parent}:{cfg.project_dir / '.openclaw' / 'bin'}:{env.get('PATH', '')}"
    env["HOME"] = str(cfg.project_dir)
    env["PREDICT_SERVER_URL"] = env_data["PREDICT_SERVER_URL"]
    env["AWP_PRIVATE_KEY"] = wallet_info["privateKey"]
    env["AWP_ADDRESS"] = wallet_info["address"]
    if env_data.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env_data["OPENAI_API_KEY"]
    if env_data.get("OPENAI_BASE_URL"):
        env["OPENAI_BASE_URL"] = env_data["OPENAI_BASE_URL"]
    if env_data.get("OPENAI_MODEL"):
        env["OPENAI_MODEL"] = env_data["OPENAI_MODEL"]
    return env, env_data, profile


def parse_predict_output(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"raw": text}


def run_predict(name: str, wallet: dict[str, Any], fleet: dict[str, Any], cfg: manager.ManagerConfig, args: list[str]) -> dict[str, Any]:
    env, env_data, _ = predict_env_for(name, wallet, fleet, cfg)
    completed = manager.run(["predict-agent", *args], env=env, cwd=cfg.project_dir, check=False)
    stderr_text = completed.stderr or ""
    stdout_text = completed.stdout or ""
    if "Signature already used" in stderr_text or "Signature already used" in stdout_text:
        time.sleep(1.5)
        completed = manager.run(["predict-agent", *args], env=env, cwd=cfg.project_dir, check=False)
        stderr_text = completed.stderr or ""
        stdout_text = completed.stdout or ""
    parsed = parse_predict_output(stdout_text)
    return {
        "instance": name,
        "returncode": completed.returncode,
        "stdout": parsed or stdout_text.strip(),
        "stderr": stderr_text.strip(),
        "env": env_data,
    }


TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")
BASE_RPC_URL = "https://mainnet.base.org"


def _find_tx_hash(text: str) -> str:
    match = TX_HASH_RE.search(text or "")
    return match.group(0) if match else ""


def _stringify_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _awp_env_and_wallet(name: str, wallet: dict[str, Any], fleet: dict[str, Any], cfg: manager.ManagerConfig):
    _, env_data, wallet_info = sync_wallet_instance(name, wallet, fleet, cfg)
    env = manager.wallet_env(env_data["AWP_AGENT_ID"], Path(env_data["AWP_WALLET_HOME"]), cfg.wallet_bin, cfg.project_dir)
    env["PREDICT_SERVER_URL"] = env_data.get("PREDICT_SERVER_URL", cfg.predict_server_url)
    return env, env_data, wallet_info


def _run_awp_action(
    *,
    name: str,
    action: str,
    cmd: list[str],
    env: dict[str, str],
    cwd: Path,
) -> dict[str, Any]:
    completed = manager.run(cmd, env=env, cwd=cwd, check=False)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    tx_hash = _find_tx_hash(stdout) or _find_tx_hash(stderr)
    chain_status = _await_base_receipt(tx_hash) if completed.returncode == 0 and tx_hash else {"status": "failed", "reason": "no_tx_hash"}
    if completed.returncode != 0:
        status = "failed"
    else:
        status = chain_status.get("status", "submitted")
    ok = completed.returncode == 0 and status != "failed"
    return {
        "instance": name,
        "action": action,
        "command": cmd,
        "commandText": " ".join(cmd),
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "txHash": tx_hash,
        "status": status,
        "chainStatus": chain_status,
        "ok": ok,
    }


def _rpc_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _await_base_receipt(tx_hash: str, timeout_seconds: int = 20) -> dict[str, Any]:
    if not tx_hash:
        return {"status": "failed", "reason": "missing_tx_hash"}
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            data = _rpc_post(
                BASE_RPC_URL,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                },
            )
            receipt = data.get("result")
            if receipt is None:
                time.sleep(1)
                continue
            receipt_status = receipt.get("status")
            if receipt_status == "0x1":
                return {"status": "success", "receipt": receipt}
            if receipt_status == "0x0":
                return {"status": "failed", "receipt": receipt}
            return {"status": "submitted", "receipt": receipt}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(1)
    return {"status": "submitted", "reason": last_error or "receipt_pending"}


def _result_ok(payload: Any) -> bool:
    if isinstance(payload, dict):
        if "ok" in payload:
            return bool(payload.get("ok"))
        if "status" in payload and payload.get("status") in {"submitted", "confirmed", "success"}:
            return True
        return True
    if isinstance(payload, list):
        if not payload:
            return False
        oks = []
        for item in payload:
            if isinstance(item, dict):
                if "ok" in item:
                    oks.append(bool(item.get("ok")))
                elif "status" in item:
                    oks.append(item.get("status") in {"submitted", "confirmed", "success"})
        return bool(oks) and all(oks)
    return False


def monitor_dashboard(env_data: dict[str, str]) -> dict[str, Any]:
    host = env_data["AWP_PREDICT_MONITOR_HOST"]
    port = env_data["AWP_PREDICT_MONITOR_PORT"]
    url = f"http://{host}:{port}/api/dashboard"
    req = urllib.request.Request(url, headers={"User-Agent": "awp-predict-fleet"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
            if not isinstance(payload, dict):
                return {"url": url, "ok": False, "error": "non-object dashboard response"}
            return {"url": url, "ok": True, "data": payload}
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def pick_wallets(args: argparse.Namespace, fleet: dict[str, Any]) -> list[str]:
    wallets = sorted(fleet.get("wallets", {}).keys())
    if getattr(args, "all", False):
        return wallets
    if getattr(args, "name", None):
        return [args.name]
    if len(wallets) == 1:
        return wallets
    if not wallets:
        die("no wallets registered")
    die("specify a wallet name or use --all")
    return []


def cmd_open(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    name = args.name
    if args.monitor_port:
        validate_assignment_port(name, args.monitor_port, fleet)
        manager.validate_monitor_port(args.monitor_port, cfg, ignore_instance=name)
    if args.profile and args.profile not in fleet["profiles"]:
        fleet["profiles"][args.profile] = {}
    wallet = ensure_wallet_registered(
        name,
        fleet,
        cfg,
        wallet_home=args.wallet_home,
        agent_id=args.agent_id,
        profile=args.profile,
        monitor_port=args.monitor_port,
    )
    if args.model or args.base_url or args.api_key or args.predict_server_url or args.loop_interval:
        overrides = wallet.setdefault("overrides", {})
        if args.model:
            overrides.setdefault("openai", {})["model"] = args.model
        if args.base_url:
            overrides.setdefault("openai", {})["base_url"] = args.base_url
        if args.api_key:
            overrides.setdefault("openai", {})["api_key"] = args.api_key
        if args.predict_server_url:
            overrides["predict_server_url"] = args.predict_server_url
        if args.loop_interval:
            overrides["loop_interval"] = args.loop_interval
    _, env_data, wallet_info = sync_wallet_instance(name, wallet, fleet, cfg)
    save_fleet(fleet_file, fleet)
    print(json.dumps({
        "instance": name,
        "fleetFile": str(fleet_file),
        "configFile": str(manager.config_file_for(name, cfg)),
        "envFile": str(manager.env_file_for(name, cfg)),
        "address": wallet_info["address"],
        "privateKey": wallet_info["privateKey"] if wallet_info["created"] else "",
        "createdWallet": wallet_info["created"],
        "profile": wallet.get("profile", "default"),
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        "message": "Wallet ready. Transfer AWP, stake manually, then start predict.",
    }, ensure_ascii=False, indent=2))


def cmd_profile_set(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    profile = fleet["profiles"].setdefault(args.profile, {})
    openai = profile.setdefault("openai", {})
    changed = False
    if args.model:
        openai["model"] = args.model
        changed = True
    if args.base_url:
        openai["base_url"] = args.base_url
        changed = True
    if args.api_key:
        openai["api_key"] = args.api_key
        changed = True
    if args.predict_server_url:
        profile["predict_server_url"] = args.predict_server_url
        changed = True
    if args.loop_interval:
        profile["loop_interval"] = args.loop_interval
        changed = True
    if not changed:
        die("no profile changes provided")
    save_fleet(fleet_file, fleet)
    print(json.dumps({"profile": args.profile, "updated": True, "fleetFile": str(fleet_file)}, ensure_ascii=False, indent=2))


def cmd_profile_list(args: argparse.Namespace) -> None:
    _, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    print(json.dumps(fleet.get("profiles", {}), ensure_ascii=False, indent=2))


def cmd_wallet_config(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    wallets = fleet.setdefault("wallets", {})
    if args.name not in wallets:
        die(f"wallet not registered: {args.name}")
    wallet = wallets[args.name]
    if args.profile:
        if args.profile not in fleet["profiles"]:
            fleet["profiles"][args.profile] = {}
        wallet["profile"] = args.profile
    if args.wallet_home:
        wallet["wallet_home"] = args.wallet_home
    if args.agent_id:
        wallet["agent_id"] = args.agent_id
    if args.monitor_port:
        validate_assignment_port(args.name, args.monitor_port, fleet)
        manager.validate_monitor_port(args.monitor_port, cfg, ignore_instance=args.name)
        wallet["monitor_port"] = args.monitor_port
    overrides = wallet.setdefault("overrides", {})
    if args.model:
        overrides.setdefault("openai", {})["model"] = args.model
    if args.base_url:
        overrides.setdefault("openai", {})["base_url"] = args.base_url
    if args.api_key:
        overrides.setdefault("openai", {})["api_key"] = args.api_key
    if args.predict_server_url:
        overrides["predict_server_url"] = args.predict_server_url
    if args.loop_interval:
        overrides["loop_interval"] = args.loop_interval
    _, env_data, wallet_info = sync_wallet_instance(args.name, wallet, fleet, cfg)
    save_fleet(fleet_file, fleet)
    print(json.dumps({
        "instance": args.name,
        "profile": wallet.get("profile", "default"),
        "address": wallet_info["address"],
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        "fleetFile": str(fleet_file),
    }, ensure_ascii=False, indent=2))


def cmd_wallet_list(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    wallets = fleet.setdefault("wallets", {})
    discovered_names: set[str] = set(wallets.keys())
    if cfg.env_dir.exists():
        for env_file in cfg.env_dir.glob("*.env"):
            discovered_names.add(env_file.stem)
    if cfg.wallet_base_dir.exists():
        for child in cfg.wallet_base_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                discovered_names.add(child.name)

    rows = []
    for name in sorted(discovered_names):
        wallet = wallets.get(name, {})
        if not isinstance(wallet, dict):
            wallet = {}
        if name not in wallets:
            wallets[name] = wallet
        settings = resolve_wallet_settings(name, wallet, fleet, cfg)
        address = wallet.get("address", "")
        if not address:
            cfg_path = manager.config_file_for(name, cfg)
            if cfg_path.exists():
                cfg_json = manager.load_json_file(cfg_path)
                address = ((cfg_json.get("wallet") or {}).get("address") or "")
        if not address:
            address = recover_address_from_wallet_dir(name, cfg)
            if address:
                wallet["address"] = address
        rows.append({
            "instance": name,
            "profile": wallet.get("profile", "default"),
            "address": address,
            "wallet_home": settings["wallet_home"],
            "agent_id": settings["agent_id"],
            "model": (settings.get("openai") or {}).get("model"),
            "monitor_port": (settings.get("monitor") or {}).get("port"),
        })
    deduped: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        address_key = str(row.get("address") or "").strip().lower()
        wallet_home_key = str(row.get("wallet_home") or "").strip()
        dedupe_key = f"addr:{address_key}" if address_key else f"home:{wallet_home_key}"
        existing = seen.get(dedupe_key)
        if not existing:
            seen[dedupe_key] = row
            deduped.append(row)
            continue
        score_existing = sum(bool(existing.get(k)) for k in ("address", "profile", "agent_id", "model", "monitor_port"))
        score_new = sum(bool(row.get(k)) for k in ("address", "profile", "agent_id", "model", "monitor_port"))
        if score_new > score_existing:
            idx = deduped.index(existing)
            deduped[idx] = row
            seen[dedupe_key] = row
    save_fleet(fleet_file, fleet)
    print(json.dumps({"fleetFile": str(fleet_file), "wallets": deduped}, ensure_ascii=False, indent=2))


def cmd_delete(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        manager.stop_instance(name, cfg)
        fleet.get("wallets", {}).pop(name, None)
        env_file = cfg.env_dir / f"{name}.env"
        wallet_dir = cfg.wallet_base_dir / name
        config_file = manager.config_file_for(name, cfg)
        for path in [env_file, config_file]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if wallet_dir.exists():
            shutil.rmtree(wallet_dir, ignore_errors=True)
        out.append({"instance": name, "deleted": True})
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_start(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    if manager.use_systemd():
        manager.install_systemd_units(cfg)
        manager.run(["systemctl", "enable", "--now", manager.DEFAULT_GATEWAY_SERVICE])
    for name in targets:
        wallet = fleet["wallets"][name]
        _, env_data, _ = sync_wallet_instance(name, wallet, fleet, cfg)
        if not env_data.get("OPENAI_API_KEY"):
            out.append({"instance": name, "started": False, "error": "OPENAI_API_KEY is empty"})
            continue
        manager.start_instance(name, cfg)
        out.append({"instance": name, "started": True, "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/"})
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_stop(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        manager.stop_instance(name, cfg)
        out.append({"instance": name, "stopped": True})
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        _, env_data, _ = sync_wallet_instance(name, wallet, fleet, cfg)
        if manager.use_systemd():
            loop_service, monitor_service = manager.instance_services(name)
            loop = manager.run(["systemctl", "is-active", loop_service], check=False).stdout.strip()
            monitor = manager.run(["systemctl", "is-active", monitor_service], check=False).stdout.strip()
            gateway = manager.run(["systemctl", "is-active", manager.DEFAULT_GATEWAY_SERVICE], check=False).stdout.strip()
        else:
            loop = manager.local_service_status(name, cfg, "loop")
            monitor = manager.local_service_status(name, cfg, "monitor")
            gateway = "not-used-local"
        out.append({
            "instance": name,
            "profile": wallet.get("profile", "default"),
            "gateway": gateway,
            "loop": loop,
            "monitor": monitor,
            "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_balance(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        result = run_predict(name, wallet, fleet, cfg, ["status"])
        data = ((result.get("stdout") or {}).get("data") or {}) if isinstance(result.get("stdout"), dict) else {}
        out.append({
            "instance": name,
            "balance": data.get("balance"),
            "pending_orders": data.get("pending_orders"),
            "total_predictions": data.get("total_predictions"),
            "returncode": result["returncode"],
            "stderr": result["stderr"],
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_history(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        result = run_predict(name, wallet, fleet, cfg, ["history"])
        payload = result.get("stdout") if isinstance(result.get("stdout"), dict) else {}
        data = payload.get("data") or {}
        predictions = data.get("predictions") or []
        out.append({
            "instance": name,
            "summary": data.get("summary") or {},
            "count": len(predictions),
            "predictions": predictions[: args.limit],
            "returncode": result["returncode"],
            "stderr": result["stderr"],
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_health(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        _, env_data, _ = sync_wallet_instance(name, wallet, fleet, cfg)
        if manager.use_systemd():
            loop_service, monitor_service = manager.instance_services(name)
            loop = manager.run(["systemctl", "is-active", loop_service], check=False).stdout.strip()
            monitor = manager.run(["systemctl", "is-active", monitor_service], check=False).stdout.strip()
            gateway = manager.run(["systemctl", "is-active", manager.DEFAULT_GATEWAY_SERVICE], check=False).stdout.strip()
        else:
            loop = manager.local_service_status(name, cfg, "loop")
            monitor = manager.local_service_status(name, cfg, "monitor")
            gateway = "not-used-local"
        dashboard = monitor_dashboard(env_data)
        data = dashboard.get("data") if dashboard.get("ok") else {}
        runtime_status = ((data or {}).get("runtime") or {}).get("status") or {}
        normal = loop == "active" and monitor == "active" and dashboard.get("ok", False)
        out.append({
            "instance": name,
            "normal": normal,
            "gateway": gateway,
            "loop": loop,
            "monitor": monitor,
            "dashboard": dashboard.get("ok"),
            "monitorUrl": dashboard.get("url"),
            "balance": runtime_status.get("balance"),
            "submissions_used": ((runtime_status.get("timeslot") or {}).get("submissions_used")),
            "slot_limit": ((runtime_status.get("timeslot") or {}).get("slot_limit")),
            "submissions_remaining": ((runtime_status.get("timeslot") or {}).get("submissions_remaining")),
            "pending_orders": runtime_status.get("pending_orders"),
            "error": dashboard.get("error", ""),
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_stake(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        env, _env_data, _wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        script_name = "onchain-stake.py" if args.mode == "onchain" else "relay-stake.py"
        script_path = cfg.skill_scripts_dir / script_name
        manager.ensure_file(script_path)
        cmd = [
            "/usr/bin/python3",
            str(script_path),
            "--amount",
            str(args.amount),
            "--lock-days",
            str(args.lock_days),
        ]
        if args.token:
            cmd[2:2] = ["--token", args.token]
        out.append(_run_awp_action(name=name, action="stake", cmd=cmd, env=env, cwd=cfg.project_dir))
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_register(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        env, _env_data, _wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        script_path = cfg.skill_scripts_dir / "relay-start.py"
        manager.ensure_file(script_path)
        cmd = ["/usr/bin/python3", str(script_path), "--mode", "principal"]
        if args.token:
            cmd[2:2] = ["--token", args.token]
        completed = manager.run(cmd, env=env, cwd=cfg.project_dir, check=False)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        payload = {}
        if stdout:
            try:
                payload = json.loads(stdout)
            except Exception:
                payload = {"raw": stdout}
        out.append({
            "instance": name,
            "action": "register",
            "command": cmd,
            "commandText": " ".join(cmd),
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "status": "success" if completed.returncode == 0 else "failed",
            "ok": completed.returncode == 0,
            "result": payload,
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_awp_status(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        _env, _env_data, wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        payload = _awp_chain_status(wallet_info["address"], cfg)
        out.append({
            "instance": name,
            "address": wallet_info["address"],
            "returncode": 0 if "error" not in payload else 1,
            "stdout": json.dumps(payload, ensure_ascii=False),
            "stderr": "",
            "status": payload,
        })
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _awp_chain_status(address: str, cfg: manager.ManagerConfig) -> dict[str, Any]:
    runtime_root = cfg.wallet_bin.parent.parent
    node_bin = runtime_root / "node" / "bin" / "node"
    awp_wallet_dir = runtime_root / "awp-wallet"
    if not node_bin.exists() or not awp_wallet_dir.exists():
        return {"error": "runtime node/awp-wallet not available for chain direct read", "address": address}
    script = f"""
const {{ ethers }} = require('ethers');
const provider = new ethers.JsonRpcProvider('https://mainnet.base.org');
const addr = '{address}';
const ve = new ethers.Contract('0x0000b534C63D78212f1BDCc315165852793A00A8', [
  'function getUserTotalStaked(address) view returns (uint256)'
], provider);
const alloc = new ethers.Contract('0x0000D6BB5e040E35081b3AaF59DD71b21C9800AA', [
  'function userTotalAllocated(address) view returns (uint256)',
  'function getAgentStake(address,address,uint256) view returns (uint256)',
  'function getAgentWorknets(address,address) view returns (uint256[])'
], provider);
(async()=>{{
  const totalStaked = await ve.getUserTotalStaked(addr);
  const totalAllocated = await alloc.userTotalAllocated(addr);
  let predictStake = 0n;
  let worknets = [];
  try {{
    predictStake = await alloc.getAgentStake(addr, addr, 845300000003n);
  }} catch (_err) {{
    predictStake = 0n;
  }}
  try {{
    worknets = await alloc.getAgentWorknets(addr, addr);
  }} catch (_err) {{
    worknets = [];
  }}
  const worknetStrings = worknets.map((wid) => wid.toString());
  if (predictStake === 0n && worknetStrings.length === 1 && worknetStrings[0] === '845300000003') {{
    predictStake = totalAllocated;
  }}
  const out = {{
    address: addr,
    registered: null,
    balance: {{
      totalStaked: ethers.formatEther(totalStaked),
      totalAllocated: ethers.formatEther(totalAllocated),
      unallocated: ethers.formatEther(totalStaked - totalAllocated),
      totalStaked_wei: totalStaked.toString(),
      totalAllocated_wei: totalAllocated.toString(),
      unallocated_wei: (totalStaked - totalAllocated).toString()
    }},
    allocations: worknetStrings.map((wid) => ({{
      worknetId: wid.toString(),
      amount: wid.toString() === '845300000003' ? ethers.formatEther(predictStake) : null
    }})),
    predictStake: ethers.formatEther(predictStake),
    predictStake_wei: predictStake.toString()
  }};
  console.log(JSON.stringify(out));
}})().catch((err)=>{{ console.error(String(err)); process.exit(1); }});
"""
    env = os.environ.copy()
    env["NODE_PATH"] = str(awp_wallet_dir / "node_modules")
    completed = manager.run([str(node_bin), "-e", script], env=env, cwd=awp_wallet_dir, check=False)
    if completed.returncode != 0:
        return {"error": completed.stderr.strip() or completed.stdout.strip() or "chain direct read failed", "address": address}
    try:
        payload = json.loads(completed.stdout.strip())
    except Exception:
        return {"error": "invalid chain direct read output", "raw": completed.stdout.strip(), "address": address}
    return payload


def cmd_allocate(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        env, _env_data, wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        script_name = "onchain-allocate.py" if args.mode == "onchain" else "relay-allocate.py"
        script_path = cfg.skill_scripts_dir / script_name
        manager.ensure_file(script_path)
        cmd = [
            "/usr/bin/python3",
            str(script_path),
        ]
        if args.mode == "onchain":
            if args.token:
                cmd.extend(["--token", args.token])
            cmd.extend([
                "--agent",
                args.agent or wallet_info["address"],
                "--worknet",
                str(args.worknet or manager.DEFAULT_WORKNET_ID),
                "--amount",
                str(args.amount),
            ])
        else:
            if args.token:
                cmd.extend(["--token", args.token])
            cmd.extend([
                "--mode",
                "allocate",
                "--agent",
                args.agent or wallet_info["address"],
                "--worknet",
                str(args.worknet or manager.DEFAULT_WORKNET_ID),
                "--amount",
                str(args.amount),
            ])
        out.append(_run_awp_action(name=name, action="allocate", cmd=cmd, env=env, cwd=cfg.project_dir))
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_deallocate(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        env, _env_data, wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        script_name = "onchain-deallocate.py" if args.mode == "onchain" else "relay-allocate.py"
        script_path = cfg.skill_scripts_dir / script_name
        manager.ensure_file(script_path)
        cmd = ["/usr/bin/python3", str(script_path)]
        if args.mode == "onchain":
            if args.token:
                cmd.extend(["--token", args.token])
            cmd.extend([
                "--agent",
                args.agent or wallet_info["address"],
                "--worknet",
                str(args.worknet or manager.DEFAULT_WORKNET_ID),
                "--amount",
                str(args.amount),
            ])
        else:
            if args.token:
                cmd.extend(["--token", args.token])
            cmd.extend([
                "--mode",
                "deallocate",
                "--agent",
                args.agent or wallet_info["address"],
                "--worknet",
                str(args.worknet or manager.DEFAULT_WORKNET_ID),
                "--amount",
                str(args.amount),
            ])
        out.append(_run_awp_action(name=name, action="deallocate", cmd=cmd, env=env, cwd=cfg.project_dir))
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_stake_allocate(args: argparse.Namespace) -> None:
    cfg, fleet_file = fleet_cfg(args)
    fleet = load_fleet(fleet_file)
    targets = pick_wallets(args, fleet)
    out = []
    for name in targets:
        wallet = fleet["wallets"][name]
        env, _env_data, wallet_info = _awp_env_and_wallet(name, wallet, fleet, cfg)
        script_name = "onchain-stake.py" if args.mode == "onchain" else "relay-stake.py"
        script_path = cfg.skill_scripts_dir / script_name
        manager.ensure_file(script_path)
        cmd = [
            "/usr/bin/python3",
            str(script_path),
            "--amount",
            str(args.amount),
            "--lock-days",
            str(args.lock_days),
            "--agent",
            args.agent or wallet_info["address"],
            "--worknet",
            str(args.worknet or manager.DEFAULT_WORKNET_ID),
        ]
        if args.token:
            cmd[2:2] = ["--token", args.token]
        out.append(_run_awp_action(name=name, action="stake_allocate", cmd=cmd, env=env, cwd=cfg.project_dir))
    save_fleet(fleet_file, fleet)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def add_common_args(parser: argparse.ArgumentParser) -> None:
    manager.add_shared_args(parser)
    parser.add_argument("--fleet-file", default=str(DEFAULT_FLEET_FILE), help="Central fleet config file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Central multi-wallet Predict fleet manager")
    sub = parser.add_subparsers(dest="command", required=True)

    open_cmd = sub.add_parser("open", help="Open/create one wallet under central fleet control")
    add_common_args(open_cmd)
    open_cmd.add_argument("name", nargs="?", default="default")
    open_cmd.add_argument("--profile", default="default", help="Shared profile name")
    open_cmd.add_argument("--wallet-home")
    open_cmd.add_argument("--agent-id")
    open_cmd.add_argument("--model")
    open_cmd.add_argument("--base-url")
    open_cmd.add_argument("--api-key")
    open_cmd.add_argument("--predict-server-url")
    open_cmd.add_argument("--loop-interval", type=int)
    open_cmd.add_argument("--monitor-port", type=int)
    open_cmd.set_defaults(func=cmd_open)

    profile_set = sub.add_parser("profile-set", help="Create or update a shared profile")
    add_common_args(profile_set)
    profile_set.add_argument("profile")
    profile_set.add_argument("--model")
    profile_set.add_argument("--base-url")
    profile_set.add_argument("--api-key")
    profile_set.add_argument("--predict-server-url")
    profile_set.add_argument("--loop-interval", type=int)
    profile_set.set_defaults(func=cmd_profile_set)

    profile_list = sub.add_parser("profile-list", help="List shared profiles")
    add_common_args(profile_list)
    profile_list.set_defaults(func=cmd_profile_list)

    wallet_cfg = sub.add_parser("wallet-config", help="Assign shared profile or wallet-specific overrides")
    add_common_args(wallet_cfg)
    wallet_cfg.add_argument("name")
    wallet_cfg.add_argument("--profile")
    wallet_cfg.add_argument("--wallet-home")
    wallet_cfg.add_argument("--agent-id")
    wallet_cfg.add_argument("--model")
    wallet_cfg.add_argument("--base-url")
    wallet_cfg.add_argument("--api-key")
    wallet_cfg.add_argument("--predict-server-url")
    wallet_cfg.add_argument("--loop-interval", type=int)
    wallet_cfg.add_argument("--monitor-port", type=int)
    wallet_cfg.set_defaults(func=cmd_wallet_config)

    wallet_list = sub.add_parser("wallet-list", help="List all wallets and their effective config")
    add_common_args(wallet_list)
    wallet_list.set_defaults(func=cmd_wallet_list)

    delete = sub.add_parser("delete", help="Delete one wallet or all wallets")
    add_common_args(delete)
    delete.add_argument("name", nargs="?")
    delete.add_argument("--all", action="store_true")
    delete.set_defaults(func=cmd_delete)

    start = sub.add_parser("start", help="Start one wallet or all wallets")
    add_common_args(start)
    start.add_argument("name", nargs="?")
    start.add_argument("--all", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="Stop one wallet or all wallets")
    add_common_args(stop)
    stop.add_argument("name", nargs="?")
    stop.add_argument("--all", action="store_true")
    stop.set_defaults(func=cmd_stop)

    status = sub.add_parser("status", help="Query systemd status for one wallet or all wallets")
    add_common_args(status)
    status.add_argument("name", nargs="?")
    status.add_argument("--all", action="store_true")
    status.set_defaults(func=cmd_status)

    balance = sub.add_parser("balance", help="Query predict balance for one wallet or all wallets")
    add_common_args(balance)
    balance.add_argument("name", nargs="?")
    balance.add_argument("--all", action="store_true")
    balance.set_defaults(func=cmd_balance)

    history = sub.add_parser("history", help="Query prediction history for one wallet or all wallets")
    add_common_args(history)
    history.add_argument("name", nargs="?")
    history.add_argument("--all", action="store_true")
    history.add_argument("--limit", type=int, default=5)
    history.set_defaults(func=cmd_history)

    health = sub.add_parser("health", help="One-click check whether one/all wallets are running normally")
    add_common_args(health)
    health.add_argument("name", nargs="?")
    health.add_argument("--all", action="store_true")
    health.set_defaults(func=cmd_health)

    awp_status = sub.add_parser("awp-status", help="Query AWP registration/staking/allocation status")
    add_common_args(awp_status)
    awp_status.add_argument("name", nargs="?")
    awp_status.add_argument("--all", action="store_true")
    awp_status.set_defaults(func=cmd_awp_status)

    register = sub.add_parser("register", help="Register wallet on AWP before staking")
    add_common_args(register)
    register.add_argument("name", nargs="?")
    register.add_argument("--all", action="store_true")
    register.add_argument("--token")
    register.set_defaults(func=cmd_register)

    stake = sub.add_parser("stake", help="Pure AWP stake for one wallet or all wallets")
    add_common_args(stake)
    stake.add_argument("name", nargs="?")
    stake.add_argument("--all", action="store_true")
    stake.add_argument("--amount", required=True)
    stake.add_argument("--lock-days", required=True, type=int)
    stake.add_argument("--mode", choices=["relay", "onchain"], default="relay")
    stake.add_argument("--token")
    stake.set_defaults(func=cmd_stake)

    allocate = sub.add_parser("allocate", help="Allocate existing veAWP to Predict for one wallet or all wallets")
    add_common_args(allocate)
    allocate.add_argument("name", nargs="?")
    allocate.add_argument("--all", action="store_true")
    allocate.add_argument("--amount", required=True)
    allocate.add_argument("--worknet", default=manager.DEFAULT_WORKNET_ID)
    allocate.add_argument("--agent")
    allocate.add_argument("--mode", choices=["relay", "onchain"], default="relay")
    allocate.add_argument("--token")
    allocate.set_defaults(func=cmd_allocate)

    deallocate = sub.add_parser("deallocate", help="Deallocate veAWP from Predict for one wallet or all wallets")
    add_common_args(deallocate)
    deallocate.add_argument("name", nargs="?")
    deallocate.add_argument("--all", action="store_true")
    deallocate.add_argument("--amount", required=True)
    deallocate.add_argument("--worknet", default=manager.DEFAULT_WORKNET_ID)
    deallocate.add_argument("--agent")
    deallocate.add_argument("--mode", choices=["relay", "onchain"], default="relay")
    deallocate.add_argument("--token")
    deallocate.set_defaults(func=cmd_deallocate)

    stake_allocate = sub.add_parser("stake-allocate", help="Stake AWP and allocate to Predict in one action")
    add_common_args(stake_allocate)
    stake_allocate.add_argument("name", nargs="?")
    stake_allocate.add_argument("--all", action="store_true")
    stake_allocate.add_argument("--amount", required=True)
    stake_allocate.add_argument("--lock-days", required=True, type=int)
    stake_allocate.add_argument("--worknet", default=manager.DEFAULT_WORKNET_ID)
    stake_allocate.add_argument("--agent")
    stake_allocate.add_argument("--mode", choices=["relay", "onchain"], default="relay")
    stake_allocate.add_argument("--token")
    stake_allocate.set_defaults(func=cmd_stake_allocate)

    return parser


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        sys.argv.extend(["open"])
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
