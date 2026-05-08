#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_DIR = SCRIPT_DIR if (SCRIPT_DIR / "predict_loop.py").exists() else Path("/srv/awp-predict")
DEFAULT_WALLET_BIN = Path("/srv/awp-miner/vendor/node/bin/awp-wallet")
DEFAULT_SKILL_SCRIPTS = Path("/srv/awp-miner/vendor/awp-skill/scripts")
DEFAULT_ENV_DIR = Path("/etc/awp-predict")
DEFAULT_SYSTEMD_DIR = Path("/etc/systemd/system")
DEFAULT_WALLET_BASE_DIR = Path("/srv/awp-wallets")
DEFAULT_PREDICT_SERVER_URL = "https://api.agentpredict.work"
DEFAULT_OPENAI_BASE_URL = "https://cdk.muyuai.top/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_MONITOR_HOST = "127.0.0.1"
DEFAULT_MONITOR_START_PORT = 8791
DEFAULT_LOOP_INTERVAL = 120
DEFAULT_WORKNET_ID = "845300000003"
DEFAULT_GATEWAY_SERVICE = "awp-predict-gateway"


@dataclass
class ManagerConfig:
    project_dir: Path
    wallet_bin: Path
    skill_scripts_dir: Path
    env_dir: Path
    systemd_dir: Path
    wallet_base_dir: Path
    predict_server_url: str
    openai_base_url: str
    openai_model: str
    monitor_host: str
    monitor_start_port: int
    loop_interval: int


def die(message: str, code: int = 1) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def run(args: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, env=env, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {' '.join(args)}")
    return completed


def parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON output: {text[:400]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("JSON output is not an object")
    return data


def ensure_file(path: Path) -> None:
    if not path.exists():
        die(f"missing required file: {path}")


def detect_project_dir(cli_value: str | None) -> Path:
    return Path(cli_value).expanduser().resolve() if cli_value else DEFAULT_PROJECT_DIR.resolve()


def build_config(args: argparse.Namespace) -> ManagerConfig:
    return ManagerConfig(
        project_dir=detect_project_dir(getattr(args, "project_dir", None)),
        wallet_bin=Path(getattr(args, "wallet_bin", DEFAULT_WALLET_BIN)).expanduser(),
        skill_scripts_dir=Path(getattr(args, "skill_scripts_dir", DEFAULT_SKILL_SCRIPTS)).expanduser(),
        env_dir=Path(getattr(args, "env_dir", DEFAULT_ENV_DIR)).expanduser(),
        systemd_dir=Path(getattr(args, "systemd_dir", DEFAULT_SYSTEMD_DIR)).expanduser(),
        wallet_base_dir=Path(getattr(args, "wallet_base_dir", DEFAULT_WALLET_BASE_DIR)).expanduser(),
        predict_server_url=getattr(args, "manager_predict_server_url", DEFAULT_PREDICT_SERVER_URL),
        openai_base_url=getattr(args, "manager_openai_base_url", DEFAULT_OPENAI_BASE_URL),
        openai_model=getattr(args, "manager_openai_model", DEFAULT_OPENAI_MODEL),
        monitor_host=getattr(args, "monitor_host", DEFAULT_MONITOR_HOST),
        monitor_start_port=int(getattr(args, "monitor_start_port", DEFAULT_MONITOR_START_PORT)),
        loop_interval=int(getattr(args, "default_loop_interval", DEFAULT_LOOP_INTERVAL)),
    )


def wallet_home_for(name: str, cfg: ManagerConfig) -> Path:
    return (cfg.wallet_base_dir / name / ".wallet").resolve()


def default_agent_id(name: str) -> str:
    return f"awp-{name}"


def env_file_for(name: str, cfg: ManagerConfig) -> Path:
    return cfg.env_dir / f"{name}.env"


def instance_dir_for(name: str, cfg: ManagerConfig) -> Path:
    return (cfg.wallet_base_dir / name).resolve()


def config_file_for(name: str, cfg: ManagerConfig) -> Path:
    return instance_dir_for(name, cfg) / "config.json"


def local_run_dir_for(name: str, cfg: ManagerConfig) -> Path:
    return instance_dir_for(name, cfg) / "run"


def local_log_path(name: str, cfg: ManagerConfig, kind: str) -> Path:
    return local_run_dir_for(name, cfg) / f"{kind}.log"


def local_pid_path(name: str, cfg: ManagerConfig, kind: str) -> Path:
    return local_run_dir_for(name, cfg) / f"{kind}.pid"


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def save_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: values[key] for key in sorted(values)}
    path.write_text("".join(f"{key}={value}\n" for key, value in ordered.items()), encoding="utf-8")


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid json config: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"json config is not an object: {path}")
    return data


def save_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def use_systemd() -> bool:
    from shutil import which

    return which("systemctl") is not None


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def local_service_status(name: str, cfg: ManagerConfig, kind: str) -> str:
    pid = read_pid(local_pid_path(name, cfg, kind))
    if is_pid_alive(pid):
        return "active"
    if kind == "monitor":
        try:
            env_data = load_instance(name, cfg)
            host = env_data.get("AWP_PREDICT_MONITOR_HOST", cfg.monitor_host)
            port = int(env_data.get("AWP_PREDICT_MONITOR_PORT", "0") or "0")
            if port > 0:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    if sock.connect_ex((host, port)) == 0:
                        return "active"
        except Exception:
            pass
    if kind == "loop":
        log_path = local_log_path(name, cfg, "loop")
        try:
            if log_path.exists() and (time.time() - log_path.stat().st_mtime) < 300:
                return "active"
        except OSError:
            pass
    return "inactive"


def local_runtime_env(name: str, cfg: ManagerConfig) -> dict[str, str]:
    env_data = load_instance(name, cfg)
    env = os.environ.copy()
    env.update(env_data)
    wallet_bin = Path(env_data.get("AWP_WALLET_BIN", str(cfg.wallet_bin)))
    path_parts = [str(cfg.project_dir), str(wallet_bin.parent)]
    current_path = env.get("PATH", "")
    if current_path:
        path_parts.append(current_path)
    env["PATH"] = ":".join(path_parts)
    env["HOME"] = str(cfg.project_dir)
    env["AWP_PREDICT_LOCAL_MODE"] = "1"
    env["AWP_PREDICT_LOCAL_RUN_DIR"] = str(local_run_dir_for(name, cfg))
    return env


def spawn_local_process(name: str, cfg: ManagerConfig, kind: str, cmd: list[str]) -> None:
    pid_file = local_pid_path(name, cfg, kind)
    existing = read_pid(pid_file)
    if is_pid_alive(existing):
        return
    run_dir = local_run_dir_for(name, cfg)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = local_log_path(name, cfg, kind)
    log_fp = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cfg.project_dir),
        env=local_runtime_env(name, cfg),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    write_pid(pid_file, proc.pid)
    log_fp.close()


def terminate_local_process(name: str, cfg: ManagerConfig, kind: str) -> None:
    pid_file = local_pid_path(name, cfg, kind)
    pid = read_pid(pid_file)
    if not is_pid_alive(pid):
        if pid_file.exists():
            pid_file.unlink()
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    for _ in range(20):
        if not is_pid_alive(pid):
            break
        time.sleep(0.2)
    if is_pid_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    if pid_file.exists():
        pid_file.unlink()


def is_port_free(port: int, host: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def allocated_ports(cfg: ManagerConfig) -> set[int]:
    ports: set[int] = set()
    if not cfg.env_dir.exists():
        return ports
    for env_file in cfg.env_dir.glob("*.env"):
        env_data = load_env_file(env_file)
        raw = env_data.get("AWP_PREDICT_MONITOR_PORT")
        if not raw:
            continue
        try:
            ports.add(int(raw))
        except ValueError:
            continue
    return ports


def next_monitor_port(cfg: ManagerConfig) -> int:
    used = allocated_ports(cfg)
    port = cfg.monitor_start_port
    while port in used or not is_port_free(port, cfg.monitor_host):
        port += 1
    return port


def validate_monitor_port(port: int, cfg: ManagerConfig, *, ignore_instance: str | None = None) -> None:
    for env_file in cfg.env_dir.glob("*.env"):
        if ignore_instance and env_file.stem == ignore_instance:
            continue
        env_data = load_env_file(env_file)
        raw = env_data.get("AWP_PREDICT_MONITOR_PORT")
        if raw and raw == str(port):
            die(f"monitor port already used by instance {env_file.stem}: {port}")
    if not is_port_free(port, cfg.monitor_host):
        die(f"monitor port is already occupied on host {cfg.monitor_host}: {port}")


def wallet_env(agent_id: str, wallet_home: Path, wallet_bin: Path, project_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{project_dir}:{wallet_bin.parent}:{env.get('PATH', '')}"
    env["HOME"] = "/root" if os.geteuid() == 0 else str(wallet_home.parent)
    env["AWP_AGENT_ID"] = agent_id
    env["AWP_WALLET_HOME"] = str(wallet_home)
    env["AWP_WALLET_BIN"] = str(wallet_bin)
    return env


def ensure_wallet(name: str, env_data: dict[str, str], cfg: ManagerConfig) -> dict[str, str]:
    wallet_home = Path(env_data["AWP_WALLET_HOME"])
    wallet_home.mkdir(parents=True, exist_ok=True)
    env = wallet_env(env_data["AWP_AGENT_ID"], wallet_home, cfg.wallet_bin, cfg.project_dir)

    def try_read_wallet() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        receive_result = run([str(cfg.wallet_bin), "receive"], env=env, check=False)
        export_result = run([str(cfg.wallet_bin), "export-private-key"], env=env, check=False)
        receive_payload = None
        export_payload = None
        if receive_result.returncode == 0 and receive_result.stdout.strip():
            try:
                receive_payload = parse_json(receive_result.stdout)
            except RuntimeError:
                receive_payload = None
        if export_result.returncode == 0 and export_result.stdout.strip():
            try:
                export_payload = parse_json(export_result.stdout)
            except RuntimeError:
                export_payload = None
        return receive_payload, export_payload

    receive, exported = try_read_wallet()
    wallet_ready = bool((receive or {}).get("eoaAddress") or (exported or {}).get("address") or (exported or {}).get("privateKey"))

    if not wallet_ready:
        init_result = run([str(cfg.wallet_bin), "init"], env=env, check=False)
        stderr = (init_result.stderr or "").strip()
        stdout = (init_result.stdout or "").strip()
        already_exists = "Wallet already exists" in stderr or "Wallet already exists" in stdout
        if init_result.returncode != 0 and not already_exists:
            raise RuntimeError(stderr or stdout or "wallet init failed")
        receive, exported = try_read_wallet()

    if not receive or not exported:
        raise RuntimeError("wallet exists but could not be read")

    return {
        "name": name,
        "agentId": env_data["AWP_AGENT_ID"],
        "walletHome": str(wallet_home),
        "address": receive.get("eoaAddress") or exported.get("address") or "",
        "privateKey": exported.get("privateKey") or "",
        "created": not wallet_ready,
    }


def profile_from_env(name: str, env_data: dict[str, str], cfg: ManagerConfig) -> dict[str, Any]:
    return {
        "instance": name,
        "wallet_home": env_data["AWP_WALLET_HOME"],
        "agent_id": env_data["AWP_AGENT_ID"],
        "predict_server_url": env_data.get("PREDICT_SERVER_URL", cfg.predict_server_url),
        "openai": {
            "model": env_data.get("OPENAI_MODEL", cfg.openai_model),
            "base_url": env_data.get("OPENAI_BASE_URL", cfg.openai_base_url),
            "api_key": env_data.get("OPENAI_API_KEY", ""),
        },
        "monitor": {
            "host": env_data.get("AWP_PREDICT_MONITOR_HOST", cfg.monitor_host),
            "port": int(env_data.get("AWP_PREDICT_MONITOR_PORT", cfg.monitor_start_port)),
        },
        "loop_interval": int(env_data.get("PREDICT_LOOP_INTERVAL", cfg.loop_interval)),
        "services": {
            "gateway": env_data.get("AWP_PREDICT_GATEWAY_SERVICE", DEFAULT_GATEWAY_SERVICE),
            "loop": env_data.get("AWP_PREDICT_LOOP_SERVICE", f"awp-predict-loop@{name}"),
            "monitor": env_data.get("AWP_PREDICT_MONITOR_SERVICE", f"awp-predict-monitor@{name}"),
            "journal_loop": env_data.get("AWP_PREDICT_LOOP_JOURNAL_UNIT", f"awp-predict-loop@{name}"),
        },
    }


def env_from_profile(name: str, profile: dict[str, Any], cfg: ManagerConfig) -> dict[str, str]:
    openai = profile.get("openai") if isinstance(profile.get("openai"), dict) else {}
    monitor = profile.get("monitor") if isinstance(profile.get("monitor"), dict) else {}
    services = profile.get("services") if isinstance(profile.get("services"), dict) else {}
    return {
        "AWP_PREDICT_PROJECT_DIR": str(cfg.project_dir),
        "AWP_PREDICT_OPENCLAW_HOME": str(cfg.project_dir),
        "AWP_WALLET_BIN": str(cfg.wallet_bin),
        "AWP_WALLET_HOME": str(profile.get("wallet_home") or wallet_home_for(name, cfg)),
        "AWP_AGENT_ID": str(profile.get("agent_id") or default_agent_id(name)),
        "PREDICT_SERVER_URL": str(profile.get("predict_server_url") or cfg.predict_server_url),
        "OPENAI_BASE_URL": str(openai.get("base_url") or cfg.openai_base_url),
        "OPENAI_MODEL": str(openai.get("model") or cfg.openai_model),
        "OPENAI_API_KEY": str(openai.get("api_key") or ""),
        "PREDICT_LOOP_INTERVAL": str(profile.get("loop_interval") or cfg.loop_interval),
        "AWP_PREDICT_GATEWAY_SERVICE": str(services.get("gateway") or DEFAULT_GATEWAY_SERVICE),
        "AWP_PREDICT_LOOP_SERVICE": str(services.get("loop") or f"awp-predict-loop@{name}"),
        "AWP_PREDICT_MONITOR_SERVICE": str(services.get("monitor") or f"awp-predict-monitor@{name}"),
        "AWP_PREDICT_LOOP_JOURNAL_UNIT": str(services.get("journal_loop") or f"awp-predict-loop@{name}"),
        "AWP_PREDICT_INSTANCE_NAME": name,
        "AWP_PREDICT_MONITOR_HOST": str(monitor.get("host") or cfg.monitor_host),
        "AWP_PREDICT_MONITOR_PORT": str(monitor.get("port") or next_monitor_port(cfg)),
    }


def sync_instance_files(name: str, cfg: ManagerConfig, profile: dict[str, Any]) -> dict[str, str]:
    env_data = env_from_profile(name, profile, cfg)
    save_json_file(config_file_for(name, cfg), profile)
    save_env_file(env_file_for(name, cfg), env_data)
    return env_data


def base_env(
    name: str,
    cfg: ManagerConfig,
    *,
    wallet_home: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    predict_server_url: str | None = None,
    loop_interval: int | None = None,
    monitor_port: int | None = None,
) -> dict[str, str]:
    actual_wallet_home = wallet_home or wallet_home_for(name, cfg)
    actual_agent_id = agent_id or default_agent_id(name)
    actual_port = monitor_port or next_monitor_port(cfg)
    actual_model = model or cfg.openai_model
    return {
        "AWP_PREDICT_PROJECT_DIR": str(cfg.project_dir),
        "AWP_PREDICT_OPENCLAW_HOME": str(cfg.project_dir),
        "AWP_WALLET_BIN": str(cfg.wallet_bin),
        "AWP_WALLET_HOME": str(actual_wallet_home),
        "AWP_AGENT_ID": actual_agent_id,
        "PREDICT_SERVER_URL": predict_server_url or cfg.predict_server_url,
        "OPENAI_BASE_URL": base_url or cfg.openai_base_url,
        "OPENAI_MODEL": actual_model,
        "PREDICT_LOOP_INTERVAL": str(loop_interval or cfg.loop_interval),
        "AWP_PREDICT_GATEWAY_SERVICE": DEFAULT_GATEWAY_SERVICE,
        "AWP_PREDICT_LOOP_SERVICE": f"awp-predict-loop@{name}",
        "AWP_PREDICT_MONITOR_SERVICE": f"awp-predict-monitor@{name}",
        "AWP_PREDICT_LOOP_JOURNAL_UNIT": f"awp-predict-loop@{name}",
        "AWP_PREDICT_INSTANCE_NAME": name,
        "AWP_PREDICT_MONITOR_HOST": cfg.monitor_host,
        "AWP_PREDICT_MONITOR_PORT": str(actual_port),
    }


def gateway_unit(cfg: ManagerConfig) -> str:
    return f"""[Unit]
Description=AWP Predict OpenClaw Gateway
After=network.target

[Service]
Type=simple
EnvironmentFile=-{cfg.env_dir / 'default.env'}
WorkingDirectory={cfg.project_dir}
Environment=HOME={cfg.project_dir}
ExecStart={cfg.project_dir / '.openclaw' / 'bin' / 'openclaw'} gateway run --force
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def loop_template(cfg: ManagerConfig) -> str:
    return f"""[Unit]
Description=AWP Predict Agent Loop (%i)
After=network.target {DEFAULT_GATEWAY_SERVICE}.service
Requires={DEFAULT_GATEWAY_SERVICE}.service

[Service]
Type=simple
EnvironmentFile={cfg.env_dir}/%i.env
WorkingDirectory={cfg.project_dir}
ExecStart={cfg.project_dir / 'predict-loop.sh'}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""


def monitor_template(cfg: ManagerConfig) -> str:
    return f"""[Unit]
Description=AWP Predict Monitor Dashboard (%i)
After=network.target {DEFAULT_GATEWAY_SERVICE}.service awp-predict-loop@%i.service
Requires={DEFAULT_GATEWAY_SERVICE}.service awp-predict-loop@%i.service

[Service]
Type=simple
EnvironmentFile={cfg.env_dir}/%i.env
WorkingDirectory={cfg.project_dir}
ExecStart=/usr/bin/python3 {cfg.project_dir / 'predict_monitor.py'}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_systemd_units(cfg: ManagerConfig) -> None:
    cfg.systemd_dir.mkdir(parents=True, exist_ok=True)
    (cfg.systemd_dir / "awp-predict-gateway.service").write_text(gateway_unit(cfg), encoding="utf-8")
    (cfg.systemd_dir / "awp-predict-loop@.service").write_text(loop_template(cfg), encoding="utf-8")
    (cfg.systemd_dir / "awp-predict-monitor@.service").write_text(monitor_template(cfg), encoding="utf-8")
    run(["systemctl", "daemon-reload"])


def service_name(kind: str, name: str) -> str:
    return f"awp-predict-{kind}@{name}"


def instance_services(name: str) -> tuple[str, str]:
    return service_name("loop", name), service_name("monitor", name)


def load_instance(name: str, cfg: ManagerConfig) -> dict[str, str]:
    env_file = env_file_for(name, cfg)
    if not env_file.exists():
        die(f"instance not found: {name} ({env_file})")
    return load_env_file(env_file)


def load_profile(name: str, cfg: ManagerConfig) -> dict[str, Any]:
    profile_path = config_file_for(name, cfg)
    if profile_path.exists():
        return load_json_file(profile_path)
    env_path = env_file_for(name, cfg)
    if env_path.exists():
        env_data = load_env_file(env_path)
        profile = profile_from_env(name, env_data, cfg)
        save_json_file(profile_path, profile)
        return profile
    die(f"instance not found: {name}")
    return {}


def create_or_open_instance(
    name: str,
    cfg: ManagerConfig,
    *,
    wallet_home: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    predict_server_url: str | None = None,
    loop_interval: int | None = None,
    monitor_port: int | None = None,
    overwrite: bool = False,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    config_path = config_file_for(name, cfg)
    env_path = env_file_for(name, cfg)
    created = not config_path.exists() and not env_path.exists()
    if (config_path.exists() or env_path.exists()) and not overwrite:
        profile = load_profile(name, cfg)
    else:
        if monitor_port:
            validate_monitor_port(monitor_port, cfg, ignore_instance=name if overwrite else None)
        env_data = base_env(
            name,
            cfg,
            wallet_home=wallet_home,
            agent_id=agent_id,
            model=model,
            base_url=base_url,
            predict_server_url=predict_server_url,
            loop_interval=loop_interval,
            monitor_port=monitor_port,
        )
        profile = profile_from_env(name, env_data, cfg)
        sync_instance_files(name, cfg, profile)
    env_data = sync_instance_files(name, cfg, profile)
    wallet_info = ensure_wallet(name, env_data, cfg)
    profile["wallet"] = {
        "address": wallet_info["address"],
        "created": wallet_info["created"],
    }
    sync_instance_files(name, cfg, profile)
    return profile, env_data, wallet_info


def cmd_create(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    ensure_file(cfg.project_dir / "predict_loop.py")
    ensure_file(cfg.project_dir / "predict_monitor.py")
    ensure_file(cfg.project_dir / "predict-loop.sh")
    ensure_file(cfg.wallet_bin)

    name = args.name
    _, env_data, wallet_info = create_or_open_instance(
        name,
        cfg,
        wallet_home=Path(args.wallet_home).expanduser() if args.wallet_home else None,
        agent_id=args.agent_id,
        model=args.model,
        base_url=args.base_url,
        predict_server_url=args.predict_server_url,
        loop_interval=args.loop_interval,
        monitor_port=args.monitor_port,
        overwrite=args.force,
    )

    payload = {
        "instance": name,
        "envFile": str(env_file_for(name, cfg)),
        "configFile": str(config_file_for(name, cfg)),
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        "model": env_data["OPENAI_MODEL"],
        "address": wallet_info["address"],
        "privateKey": wallet_info["privateKey"],
        "createdWallet": wallet_info["created"],
        "next": {
            "start": f"python3 {Path(__file__).name} start {name}",
            "stake": f"python3 {Path(__file__).name} stake {name} --amount 1000 --lock-days 3",
        },
    }
    if args.start:
        install_systemd_units(cfg)
        start_instance(name, cfg)
        payload["started"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_open(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    ensure_file(cfg.project_dir / "predict_loop.py")
    ensure_file(cfg.project_dir / "predict_monitor.py")
    ensure_file(cfg.project_dir / "predict-loop.sh")
    ensure_file(cfg.wallet_bin)
    _, env_data, wallet_info = create_or_open_instance(
        args.name,
        cfg,
        wallet_home=Path(args.wallet_home).expanduser() if args.wallet_home else None,
        agent_id=args.agent_id,
        model=args.model,
        base_url=args.base_url,
        predict_server_url=args.predict_server_url,
        loop_interval=args.loop_interval,
        monitor_port=args.monitor_port,
        overwrite=False,
    )
    print(json.dumps({
        "instance": args.name,
        "configFile": str(config_file_for(args.name, cfg)),
        "envFile": str(env_file_for(args.name, cfg)),
        "address": wallet_info["address"],
        "privateKey": wallet_info["privateKey"] if wallet_info["created"] else "",
        "createdWallet": wallet_info["created"],
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        "message": "Wallet ready. Transfer AWP externally, then run start when funding is done.",
    }, ensure_ascii=False, indent=2))


def start_instance(name: str, cfg: ManagerConfig) -> None:
    load_instance(name, cfg)
    if use_systemd():
        install_systemd_units(cfg)
        loop_service, monitor_service = instance_services(name)
        run(["systemctl", "enable", "--now", DEFAULT_GATEWAY_SERVICE])
        run(["systemctl", "enable", "--now", loop_service])
        run(["systemctl", "enable", "--now", monitor_service])
        return
    spawn_local_process(name, cfg, "monitor", [sys.executable, str(cfg.project_dir / "predict_monitor.py")])
    spawn_local_process(name, cfg, "loop", [sys.executable, str(cfg.project_dir / "predict_loop.py")])


def stop_instance(name: str, cfg: ManagerConfig) -> None:
    if use_systemd():
        loop_service, monitor_service = instance_services(name)
        run(["systemctl", "stop", monitor_service], check=False)
        run(["systemctl", "stop", loop_service], check=False)
        return
    terminate_local_process(name, cfg, "monitor")
    terminate_local_process(name, cfg, "loop")


def restart_instance(name: str, cfg: ManagerConfig) -> None:
    if use_systemd():
        loop_service, monitor_service = instance_services(name)
        run(["systemctl", "restart", loop_service])
        run(["systemctl", "restart", monitor_service])
        return
    stop_instance(name, cfg)
    start_instance(name, cfg)


def cmd_start(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    profile = load_profile(args.name, cfg)
    env_data = sync_instance_files(args.name, cfg, profile)
    if not env_data.get("OPENAI_API_KEY"):
        die(f"OPENAI_API_KEY is empty in {config_file_for(args.name, cfg)}")
    start_instance(args.name, cfg)
    print(json.dumps({
        "instance": args.name,
        "started": True,
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
    }, ensure_ascii=False, indent=2))


def cmd_stop(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    stop_instance(args.name, cfg)
    print(json.dumps({"instance": args.name, "stopped": True}, ensure_ascii=False, indent=2))


def cmd_restart(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    restart_instance(args.name, cfg)
    print(json.dumps({"instance": args.name, "restarted": True}, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    rows = []
    for env_file in sorted(cfg.env_dir.glob("*.env")):
        env_data = load_env_file(env_file)
        name = env_file.stem
        rows.append({
            "instance": name,
            "walletHome": env_data.get("AWP_WALLET_HOME"),
            "agentId": env_data.get("AWP_AGENT_ID"),
            "model": env_data.get("OPENAI_MODEL"),
            "monitorPort": env_data.get("AWP_PREDICT_MONITOR_PORT"),
        })
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_wallet(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    env_data = load_instance(args.name, cfg)
    wallet_info = ensure_wallet(args.name, env_data, cfg)
    payload = {
        "instance": args.name,
        "address": wallet_info["address"],
        "walletHome": wallet_info["walletHome"],
        "agentId": wallet_info["agentId"],
    }
    if args.show_private_key:
        payload["privateKey"] = wallet_info["privateKey"]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_config(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    profile = load_profile(args.name, cfg)
    openai = profile.get("openai") if isinstance(profile.get("openai"), dict) else {}
    monitor = profile.get("monitor") if isinstance(profile.get("monitor"), dict) else {}
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
    if args.monitor_port:
        validate_monitor_port(args.monitor_port, cfg, ignore_instance=args.name)
        changed = True
        monitor["port"] = args.monitor_port
    if not changed:
        die("no config changes provided")
    profile["openai"] = openai
    profile["monitor"] = monitor
    env_data = sync_instance_files(args.name, cfg, profile)
    if args.restart:
        restart_instance(args.name, cfg)
    print(json.dumps({
        "instance": args.name,
        "updated": True,
        "restarted": bool(args.restart),
        "model": env_data.get("OPENAI_MODEL"),
        "monitorPort": env_data.get("AWP_PREDICT_MONITOR_PORT"),
        "configFile": str(config_file_for(args.name, cfg)),
    }, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    env_data = load_instance(args.name, cfg)
    if use_systemd():
        loop_service, monitor_service = instance_services(args.name)
        loop = run(["systemctl", "is-active", loop_service], check=False)
        monitor = run(["systemctl", "is-active", monitor_service], check=False)
        gateway = run(["systemctl", "is-active", DEFAULT_GATEWAY_SERVICE], check=False)
        gateway_status = gateway.stdout.strip() or gateway.stderr.strip()
        loop_status = loop.stdout.strip() or loop.stderr.strip()
        monitor_status = monitor.stdout.strip() or monitor.stderr.strip()
    else:
        gateway_status = "not-used-local"
        loop_status = local_service_status(args.name, cfg, "loop")
        monitor_status = local_service_status(args.name, cfg, "monitor")
    print(json.dumps({
        "instance": args.name,
        "gateway": gateway_status,
        "loop": loop_status,
        "monitor": monitor_status,
        "monitorUrl": f"http://{env_data['AWP_PREDICT_MONITOR_HOST']}:{env_data['AWP_PREDICT_MONITOR_PORT']}/",
        "model": env_data.get("OPENAI_MODEL"),
    }, ensure_ascii=False, indent=2))


def cmd_stake(args: argparse.Namespace) -> None:
    cfg = build_config(args)
    env_data = load_instance(args.name, cfg)
    wallet_info = ensure_wallet(args.name, env_data, cfg)
    wallet_home = Path(env_data["AWP_WALLET_HOME"])
    env = wallet_env(env_data["AWP_AGENT_ID"], wallet_home, cfg.wallet_bin, cfg.project_dir)
    env["PREDICT_SERVER_URL"] = env_data.get("PREDICT_SERVER_URL", cfg.predict_server_url)

    script_name = "onchain-stake.py" if args.mode == "onchain" else "relay-stake.py"
    script_path = cfg.skill_scripts_dir / script_name
    ensure_file(script_path)
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
        str(args.worknet or DEFAULT_WORKNET_ID),
    ]
    if args.token:
        cmd[2:2] = ["--token", args.token]
    completed = run(cmd, env=env, cwd=cfg.project_dir)
    output = completed.stdout.strip() or completed.stderr.strip()
    print(json.dumps({
        "instance": args.name,
        "mode": args.mode,
        "worknet": str(args.worknet or DEFAULT_WORKNET_ID),
        "address": wallet_info["address"],
        "output": output,
    }, ensure_ascii=False, indent=2))


def add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-dir", help="Predict project directory")
    parser.add_argument("--wallet-bin", default=str(DEFAULT_WALLET_BIN), help="awp-wallet binary path")
    parser.add_argument("--skill-scripts-dir", default=str(DEFAULT_SKILL_SCRIPTS), help="AWP skill scripts directory")
    parser.add_argument("--env-dir", default=str(DEFAULT_ENV_DIR), help="Instance env directory")
    parser.add_argument("--systemd-dir", default=str(DEFAULT_SYSTEMD_DIR), help="systemd unit directory")
    parser.add_argument("--wallet-base-dir", default=str(DEFAULT_WALLET_BASE_DIR), help="Base directory for generated wallets")
    parser.add_argument("--manager-predict-server-url", default=DEFAULT_PREDICT_SERVER_URL, help="Default Predict server URL used when creating instances")
    parser.add_argument("--manager-openai-base-url", default=DEFAULT_OPENAI_BASE_URL, help="Default LLM base URL used when creating instances")
    parser.add_argument("--manager-openai-model", default=DEFAULT_OPENAI_MODEL, help="Default LLM model used when creating instances")
    parser.add_argument("--monitor-host", default=DEFAULT_MONITOR_HOST, help="Monitor bind host")
    parser.add_argument("--monitor-start-port", type=int, default=DEFAULT_MONITOR_START_PORT, help="Starting port for auto-allocation")
    parser.add_argument("--default-loop-interval", type=int, default=DEFAULT_LOOP_INTERVAL, help="Default loop interval used when creating instances")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-file AWP Predict multi-wallet manager")
    sub = parser.add_subparsers(dest="command", required=True)

    open_cmd = sub.add_parser("open", help="First-open behavior: auto create wallet if missing, otherwise reuse it")
    add_shared_args(open_cmd)
    open_cmd.add_argument("name", nargs="?", default="default", help="Instance name, default is 'default'")
    open_cmd.add_argument("--wallet-home", help="Override wallet home path")
    open_cmd.add_argument("--agent-id", help="Override AWP_AGENT_ID")
    open_cmd.add_argument("--model", help="Override OPENAI_MODEL, default gpt-5.4")
    open_cmd.add_argument("--base-url", help="Override OPENAI_BASE_URL")
    open_cmd.add_argument("--predict-server-url", help="Override PREDICT_SERVER_URL")
    open_cmd.add_argument("--loop-interval", type=int, help="Override PREDICT_LOOP_INTERVAL")
    open_cmd.add_argument("--monitor-port", type=int, help="Override monitor port")
    open_cmd.set_defaults(func=cmd_open)

    create = sub.add_parser("create", help="Create a new wallet instance and print address/private key")
    add_shared_args(create)
    create.add_argument("name", help="Instance name, e.g. wallet02")
    create.add_argument("--wallet-home", help="Override wallet home path")
    create.add_argument("--agent-id", help="Override AWP_AGENT_ID")
    create.add_argument("--model", help="Override OPENAI_MODEL, default gpt-5.4")
    create.add_argument("--base-url", help="Override OPENAI_BASE_URL")
    create.add_argument("--predict-server-url", help="Override PREDICT_SERVER_URL")
    create.add_argument("--loop-interval", type=int, help="Override PREDICT_LOOP_INTERVAL")
    create.add_argument("--monitor-port", type=int, help="Override monitor port")
    create.add_argument("--force", action="store_true", help="Overwrite existing env file")
    create.add_argument("--start", action="store_true", help="Start prediction services immediately")
    create.set_defaults(func=cmd_create)

    start = sub.add_parser("start", help="One-click start predict in background")
    add_shared_args(start)
    start.add_argument("name")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="Stop predict services for one instance")
    add_shared_args(stop)
    stop.add_argument("name")
    stop.set_defaults(func=cmd_stop)

    restart = sub.add_parser("restart", help="Restart predict services for one instance")
    add_shared_args(restart)
    restart.add_argument("name")
    restart.set_defaults(func=cmd_restart)

    status = sub.add_parser("status", help="Show background service status")
    add_shared_args(status)
    status.add_argument("name")
    status.set_defaults(func=cmd_status)

    wallet = sub.add_parser("wallet", help="Query wallet address or private key")
    add_shared_args(wallet)
    wallet.add_argument("name")
    wallet.add_argument("--show-private-key", action="store_true", help="Include private key in output")
    wallet.set_defaults(func=cmd_wallet)

    stake = sub.add_parser("stake", help="Manual AWP stake for one wallet")
    add_shared_args(stake)
    stake.add_argument("name")
    stake.add_argument("--amount", required=True, help="AWP amount")
    stake.add_argument("--lock-days", required=True, type=int, help="Lock duration in days")
    stake.add_argument("--worknet", default=DEFAULT_WORKNET_ID, help="Target worknet, default Predict WorkNet")
    stake.add_argument("--agent", help="Agent address override")
    stake.add_argument("--mode", choices=["relay", "onchain"], default="relay", help="Stake mode")
    stake.add_argument("--token", help="Optional awp-wallet session token")
    stake.set_defaults(func=cmd_stake)

    config = sub.add_parser("config", help="Update model or monitor config")
    add_shared_args(config)
    config.add_argument("name")
    config.add_argument("--model", help="Set OPENAI_MODEL, default stays gpt-5.4 unless changed")
    config.add_argument("--base-url", help="Set OPENAI_BASE_URL")
    config.add_argument("--api-key", help="Set OPENAI_API_KEY")
    config.add_argument("--predict-server-url", help="Set PREDICT_SERVER_URL")
    config.add_argument("--loop-interval", type=int, help="Set PREDICT_LOOP_INTERVAL")
    config.add_argument("--monitor-port", type=int, help="Change monitor port")
    config.add_argument("--restart", action="store_true", help="Restart services after update")
    config.set_defaults(func=cmd_config)

    list_cmd = sub.add_parser("list", help="List all managed instances")
    add_shared_args(list_cmd)
    list_cmd.set_defaults(func=cmd_list)

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
