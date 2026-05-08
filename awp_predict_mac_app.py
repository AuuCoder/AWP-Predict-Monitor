#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tarfile
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_TITLE = "AWP Predict Fleet"
SCRIPT_DIR = Path(__file__).resolve().parent
FLEET_SCRIPT = SCRIPT_DIR / "awp_predict_fleet.py"


def runtime_base_dir() -> Path:
    for parent in [SCRIPT_DIR, *SCRIPT_DIR.parents]:
        if parent.suffix == ".app":
            # Keep writable data next to the app bundle, not inside it.
            return parent.parent
    return SCRIPT_DIR


APP_BASE_DIR = runtime_base_dir()
APP_DATA_DIR = APP_BASE_DIR / "awp-predict-data"
FLEET_FILE = APP_DATA_DIR / "fleet.json"
WALLET_BASE_DIR = APP_DATA_DIR / "wallets"
ENV_DIR = APP_DATA_DIR / "env"
BUNDLED_SKILL_DIR = SCRIPT_DIR / "vendor" / "awp-skill"
USER_SKILL_DIR = Path.home() / ".codex" / "skills" / "awp-skill"
RUNTIME_DIR = APP_DATA_DIR / "runtime"
RUNTIME_BIN_DIR = RUNTIME_DIR / "bin"
RUNTIME_NODE_DIR = RUNTIME_DIR / "node"
RUNTIME_AWP_WALLET_DIR = RUNTIME_DIR / "awp-wallet"
RUNTIME_AWP_WALLET_BIN = RUNTIME_BIN_DIR / "awp-wallet"
RUNTIME_PREDICT_AGENT_BIN = RUNTIME_BIN_DIR / "predict-agent"
BUNDLED_AWP_WALLET_DIR = SCRIPT_DIR / "vendor" / "awp-wallet"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x780")
        self.minsize(980, 720)

        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        WALLET_BASE_DIR.mkdir(parents=True, exist_ok=True)
        ENV_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_BIN_DIR.mkdir(parents=True, exist_ok=True)

        self.task_queue: queue.Queue[tuple[str, dict | list | str]] = queue.Queue()
        self.wallets_data: list[dict] = []
        self.profiles_data: dict[str, dict] = {}
        self.busy_count = 0

        self._build_ui()
        self.after(150, self._drain_queue)
        self.after(200, self.bootstrap_first_run)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text=APP_TITLE, font=("SF Pro Text", 18, "bold")).pack(anchor="w")
        ttk.Label(
            top,
            text=f"Data dir: {APP_DATA_DIR}",
            foreground="#666666",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            top,
            text=f"Fleet file: {FLEET_FILE}",
            foreground="#666666",
        ).pack(anchor="w")

        status_row = ttk.Frame(top)
        status_row.pack(fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(status_row, textvariable=self.status_var)
        self.status_label.pack(side="left")
        self.progress = ttk.Progressbar(status_row, mode="indeterminate", length=220)
        self.progress.pack(side="right")

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        upper = ttk.Frame(body, padding=6)
        lower = ttk.Frame(body, padding=6)
        body.add(upper, weight=3)
        body.add(lower, weight=2)

        upper.columnconfigure(0, weight=1)
        upper.columnconfigure(1, weight=1)

        wallet_box = ttk.LabelFrame(upper, text="Wallet Fleet", padding=12)
        wallet_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        profile_box = ttk.LabelFrame(upper, text="Profiles And Config", padding=12)
        profile_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        upper.rowconfigure(0, weight=1)
        upper.columnconfigure(0, weight=1)
        upper.columnconfigure(1, weight=1)

        self._build_wallet_box(wallet_box)
        self._build_profile_box(profile_box)

        console_box = ttk.LabelFrame(lower, text="Output", padding=8)
        console_box.pack(fill="both", expand=True)

        actions = ttk.Frame(console_box)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Refresh Fleet", command=self.refresh_all).pack(side="left")
        ttk.Button(actions, text="Check All Health", command=lambda: self.run_named("health-all", self.cmd_health_all)).pack(side="left", padx=8)
        ttk.Button(actions, text="List Wallets", command=lambda: self.run_named("wallet-list", self.cmd_wallet_list)).pack(side="left")
        ttk.Button(actions, text="List Profiles", command=lambda: self.run_named("profile-list", self.cmd_profile_list)).pack(side="left", padx=8)

        self.output = ScrolledText(console_box, wrap="word", font=("Menlo", 12))
        self.output.pack(fill="both", expand=True)

    def _build_wallet_box(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Wallet").grid(row=0, column=0, sticky="w")
        self.wallet_var = tk.StringVar(value="default")
        self.wallet_combo = ttk.Combobox(parent, textvariable=self.wallet_var, state="normal")
        self.wallet_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.wallet_combo.bind("<<ComboboxSelected>>", lambda _e: self.sync_form_from_selection())

        ttk.Label(parent, text="Profile").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.wallet_profile_var = tk.StringVar(value="default")
        self.wallet_profile_combo = ttk.Combobox(parent, textvariable=self.wallet_profile_var, state="normal")
        self.wallet_profile_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.wallet_profile_combo.bind("<<ComboboxSelected>>", lambda _e: self.sync_form_from_selection())

        ttk.Label(parent, text="Wallet Home").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.wallet_home_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.wallet_home_var).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Agent ID").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.agent_id_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.agent_id_var).grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Monitor Port").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.monitor_port_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.monitor_port_var).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Stake Amount").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.stake_amount_var = tk.StringVar(value="1000")
        ttk.Entry(parent, textvariable=self.stake_amount_var).grid(row=5, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Lock Days").grid(row=6, column=0, sticky="w", pady=(8, 0))
        self.lock_days_var = tk.StringVar(value="3")
        ttk.Entry(parent, textvariable=self.lock_days_var).grid(row=6, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row = ttk.Frame(parent)
        row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for idx, (label, command) in enumerate(
            [
                ("Open/Init", self.cmd_open_wallet),
                ("Save Wallet Config", self.cmd_wallet_config),
                ("Show Wallet", self.cmd_show_wallet),
                ("Stake", self.cmd_stake),
            ]
        ):
            ttk.Button(row, text=label, command=lambda c=command: self.run_named(label, c)).grid(row=0, column=idx, padx=(0 if idx == 0 else 6, 0))

        row2 = ttk.Frame(parent)
        row2.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for idx, (label, command) in enumerate(
            [
                ("Start", self.cmd_start_wallet),
                ("Stop", self.cmd_stop_wallet),
                ("Status", self.cmd_status_wallet),
                ("Balance", self.cmd_balance_wallet),
                ("History", self.cmd_history_wallet),
                ("Health", self.cmd_health_wallet),
            ]
        ):
            ttk.Button(row2, text=label, command=lambda c=command: self.run_named(label, c)).grid(row=0, column=idx, padx=(0 if idx == 0 else 6, 0))

    def _build_profile_box(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Profile").grid(row=0, column=0, sticky="w")
        self.profile_var = tk.StringVar(value="default")
        self.profile_combo = ttk.Combobox(parent, textvariable=self.profile_var, state="normal")
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _e: self.sync_form_from_selection())

        ttk.Label(parent, text="Model").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.model_var = tk.StringVar(value="gpt-5.4")
        ttk.Entry(parent, textvariable=self.model_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Base URL").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.base_url_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.base_url_var).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="API Key").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.api_key_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.api_key_var, show="*").grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Predict Server URL").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.predict_server_var = tk.StringVar(value="https://api.agentpredict.work")
        ttk.Entry(parent, textvariable=self.predict_server_var).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(parent, text="Loop Interval").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.loop_interval_var = tk.StringVar(value="120")
        ttk.Entry(parent, textvariable=self.loop_interval_var).grid(row=5, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row = ttk.Frame(parent)
        row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(row, text="Save Shared Profile", command=lambda: self.run_named("profile-save", self.cmd_save_profile)).pack(side="left")
        ttk.Button(row, text="Apply To Wallet", command=lambda: self.run_named("apply-profile", self.cmd_apply_profile_to_wallet)).pack(side="left", padx=8)

        hint = ttk.Label(
            parent,
            text="同一个 profile 可以给多个钱包共用。单个钱包也可以再单独覆盖。",
            foreground="#666666",
            wraplength=420,
        )
        hint.grid(row=7, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def common_args(self) -> list[str]:
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

    def fleet_call(self, args: list[str]) -> dict | list | str:
        cmd = [sys.executable, str(FLEET_SCRIPT), *args, *self.common_args()]
        env = os.environ.copy()
        env["PATH"] = f"{RUNTIME_BIN_DIR}:{env.get('PATH', '')}"
        completed = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "command failed")
        text = completed.stdout.strip()
        return json.loads(text) if text else {}

    def run_named(self, title: str, func) -> None:
        self.log(f"\n=== {title} ===\n")
        self.begin_busy(f"正在执行：{title}...")
        threading.Thread(target=self._worker, args=(title, func), daemon=True).start()

    def _worker(self, title: str, func) -> None:
        try:
            result = func()
            self.task_queue.put(("result", {"title": title, "payload": result}))
        except Exception as exc:  # noqa: BLE001
            self.task_queue.put(("error", {"title": title, "payload": str(exc)}))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, data = self.task_queue.get_nowait()
                if kind == "result":
                    self.log(json.dumps(data["payload"], ensure_ascii=False, indent=2) + "\n")
                    self.end_busy(f"已完成：{data['title']}")
                    self.refresh_local_lists()
                    payload = data["payload"]
                    if isinstance(payload, dict) and payload.get("createdWallet") and payload.get("privateKey"):
                        self.show_private_key_dialog(payload.get("instance", "default"), payload["privateKey"])
                else:
                    self.log(f"ERROR: {data['payload']}\n")
                    self.end_busy(f"执行失败：{data['title']}")
        except queue.Empty:
            pass
        self.after(150, self._drain_queue)

    def log(self, text: str) -> None:
        self.output.insert("end", text)
        self.output.see("end")

    def begin_busy(self, message: str) -> None:
        self.busy_count += 1
        self.status_var.set(message)
        if self.busy_count == 1:
            self.progress.start(10)
        self.update_idletasks()

    def end_busy(self, message: str) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        self.status_var.set(message if self.busy_count == 0 else self.status_var.get())
        if self.busy_count == 0:
            self.progress.stop()
        self.update_idletasks()

    def refresh_local_lists(self) -> None:
        try:
            wallets = self.fleet_call(["wallet-list"])
            profiles = self.fleet_call(["profile-list"])
        except Exception:
            return
        wallet_rows = wallets.get("wallets", []) if isinstance(wallets, dict) else []
        self.wallets_data = wallet_rows
        self.profiles_data = profiles if isinstance(profiles, dict) else {}
        wallet_names = [row["instance"] for row in wallet_rows]
        profile_names = sorted(self.profiles_data.keys()) or ["default"]
        self.wallet_combo["values"] = wallet_names
        self.wallet_profile_combo["values"] = profile_names
        self.profile_combo["values"] = profile_names
        if wallet_names and self.wallet_var.get() not in wallet_names:
            self.wallet_var.set(wallet_names[0])
        if profile_names and self.profile_var.get() not in profile_names:
            self.profile_var.set(profile_names[0])
        self.sync_form_from_selection()

    def sync_form_from_selection(self) -> None:
        current_wallet = self.wallet_var.get().strip()
        for row in self.wallets_data:
            if row.get("instance") == current_wallet:
                self.wallet_profile_var.set(row.get("profile", "default"))
                self.wallet_home_var.set(row.get("wallet_home", ""))
                self.agent_id_var.set(row.get("agent_id", ""))
                self.monitor_port_var.set(str(row.get("monitor_port", "")))
                break
        profile_name = self.profile_var.get().strip() or self.wallet_profile_var.get().strip() or "default"
        profile = self.profiles_data.get(profile_name, {})
        openai = profile.get("openai", {}) if isinstance(profile.get("openai"), dict) else {}
        self.model_var.set(openai.get("model", "gpt-5.4"))
        self.base_url_var.set(openai.get("base_url", ""))
        self.api_key_var.set(openai.get("api_key", ""))
        self.predict_server_var.set(profile.get("predict_server_url", "https://api.agentpredict.work"))
        self.loop_interval_var.set(str(profile.get("loop_interval", 120)))

    def bootstrap_first_run(self) -> None:
        self.status_var.set("正在检查本地环境...")
        self.ensure_local_dependencies()
        if not FLEET_FILE.exists():
            self.run_named("首次启动：创建默认钱包", lambda: self.fleet_call(["open"]))
            return
        try:
            wallets = self.fleet_call(["wallet-list"])
        except Exception as exc:  # noqa: BLE001
            self.log(f"Bootstrap check failed: {exc}\n")
            self.status_var.set("初始化检查失败")
            return
        wallet_rows = wallets.get("wallets", []) if isinstance(wallets, dict) else []
        if not wallet_rows:
            self.run_named("首次启动：创建默认钱包", lambda: self.fleet_call(["open"]))
        else:
            self.refresh_local_lists()
            self.status_var.set("已加载现有钱包")

    def ensure_local_dependencies(self) -> None:
        self.ensure_bundled_skill_installed()
        self.ensure_predict_agent_installed()
        self.ensure_node_runtime_installed()
        self.ensure_awp_wallet_installed()

    def ensure_bundled_skill_installed(self) -> None:
        if USER_SKILL_DIR.exists() or not BUNDLED_SKILL_DIR.exists():
            return
        self.status_var.set("正在安装必需的 awp-skill...")
        self.update_idletasks()
        USER_SKILL_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BUNDLED_SKILL_DIR, USER_SKILL_DIR, dirs_exist_ok=True)
        self.log(f"Installed bundled awp-skill to {USER_SKILL_DIR}\n")
        self.status_var.set("awp-skill 安装完成")
        self.update_idletasks()

    def ensure_predict_agent_installed(self) -> None:
        if RUNTIME_PREDICT_AGENT_BIN.exists():
            return
        self.status_var.set("正在准备 predict-agent...")
        self.update_idletasks()
        meta = self.fetch_json("https://api.github.com/repos/awp-worknet/prediction-skill/releases/latest")
        tag = meta.get("tag_name")
        if not tag:
            raise RuntimeError("无法获取 predict-agent 最新版本")
        os_name = "darwin"
        arch = "aarch64" if os.uname().machine in {"arm64", "aarch64"} else "x86_64"
        binary = f"predict-agent-{os_name}-{arch}"
        url = f"https://github.com/awp-worknet/prediction-skill/releases/download/{tag}/{binary}"
        data = self.fetch_bytes(url)
        RUNTIME_BIN_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_PREDICT_AGENT_BIN.write_bytes(data)
        RUNTIME_PREDICT_AGENT_BIN.chmod(0o755)
        self.log(f"Installed predict-agent to {RUNTIME_PREDICT_AGENT_BIN}\n")

    def ensure_node_runtime_installed(self) -> None:
        node_bin = RUNTIME_NODE_DIR / "bin" / "node"
        if node_bin.exists():
            return
        self.status_var.set("正在准备 node runtime...")
        self.update_idletasks()
        index = self.fetch_json("https://nodejs.org/dist/index.json")
        if not isinstance(index, list):
            raise RuntimeError("无法获取 Node.js 版本列表")
        major = "v22."
        match = next((item for item in index if str(item.get("version", "")).startswith(major)), None)
        if not match:
            raise RuntimeError("找不到可用的 Node 22 版本")
        version = match["version"]
        arch = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "x64"
        filename = f"node-{version}-darwin-{arch}.tar.gz"
        url = f"https://nodejs.org/dist/{version}/{filename}"
        data = self.fetch_bytes(url)
        with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tf:
            members = tf.getmembers()
            top = members[0].name.split("/", 1)[0]
            with tempfile.TemporaryDirectory() as tmpdir:
                tf.extractall(tmpdir)
                extracted = Path(tmpdir) / top
                if RUNTIME_NODE_DIR.exists():
                    shutil.rmtree(RUNTIME_NODE_DIR)
                shutil.copytree(extracted, RUNTIME_NODE_DIR)
        self.log(f"Installed node runtime to {RUNTIME_NODE_DIR}\n")

    def ensure_awp_wallet_installed(self) -> None:
        if RUNTIME_AWP_WALLET_BIN.exists():
            return
        if not BUNDLED_AWP_WALLET_DIR.exists():
            self.log("Bundled awp-wallet source missing, skipping wallet runtime prepare.\n")
            return
        self.status_var.set("正在准备 awp-wallet...")
        self.update_idletasks()
        if RUNTIME_AWP_WALLET_DIR.exists():
            shutil.rmtree(RUNTIME_AWP_WALLET_DIR)
        shutil.copytree(BUNDLED_AWP_WALLET_DIR, RUNTIME_AWP_WALLET_DIR, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        env = os.environ.copy()
        env["PATH"] = f"{RUNTIME_NODE_DIR / 'bin'}:{env.get('PATH', '')}"
        subprocess.run(
            [str(RUNTIME_NODE_DIR / "bin" / "npm"), "install", "--no-audit", "--no-fund"],
            cwd=str(RUNTIME_AWP_WALLET_DIR),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        wrapper = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"
exec \"$ROOT_DIR/node/bin/node\" \"$ROOT_DIR/awp-wallet/scripts/wallet-cli.js\" \"$@\"
"""
        RUNTIME_AWP_WALLET_BIN.write_text(wrapper, encoding="utf-8")
        RUNTIME_AWP_WALLET_BIN.chmod(0o755)
        self.log(f"Installed awp-wallet runtime to {RUNTIME_AWP_WALLET_DIR}\n")

    def fetch_json(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "awp-predict-fleet"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def fetch_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "awp-predict-fleet"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def selected_wallet(self) -> str:
        name = self.wallet_var.get().strip() or "default"
        self.wallet_var.set(name)
        return name

    def selected_profile(self) -> str:
        name = self.profile_var.get().strip() or "default"
        self.profile_var.set(name)
        return name

    def cmd_open_wallet(self):
        args = ["open", self.selected_wallet()]
        if self.wallet_profile_var.get().strip():
            args.extend(["--profile", self.wallet_profile_var.get().strip()])
        if self.wallet_home_var.get().strip():
            args.extend(["--wallet-home", self.wallet_home_var.get().strip()])
        if self.agent_id_var.get().strip():
            args.extend(["--agent-id", self.agent_id_var.get().strip()])
        if self.monitor_port_var.get().strip():
            args.extend(["--monitor-port", self.monitor_port_var.get().strip()])
        return self.fleet_call(args)

    def cmd_save_profile(self):
        args = ["profile-set", self.selected_profile()]
        if self.model_var.get().strip():
            args.extend(["--model", self.model_var.get().strip()])
        if self.base_url_var.get().strip():
            args.extend(["--base-url", self.base_url_var.get().strip()])
        if self.api_key_var.get().strip():
            args.extend(["--api-key", self.api_key_var.get().strip()])
        if self.predict_server_var.get().strip():
            args.extend(["--predict-server-url", self.predict_server_var.get().strip()])
        if self.loop_interval_var.get().strip():
            args.extend(["--loop-interval", self.loop_interval_var.get().strip()])
        return self.fleet_call(args)

    def cmd_apply_profile_to_wallet(self):
        args = ["wallet-config", self.selected_wallet(), "--profile", self.selected_profile()]
        return self.fleet_call(args)

    def cmd_wallet_config(self):
        args = ["wallet-config", self.selected_wallet()]
        if self.wallet_profile_var.get().strip():
            args.extend(["--profile", self.wallet_profile_var.get().strip()])
        if self.wallet_home_var.get().strip():
            args.extend(["--wallet-home", self.wallet_home_var.get().strip()])
        if self.agent_id_var.get().strip():
            args.extend(["--agent-id", self.agent_id_var.get().strip()])
        if self.monitor_port_var.get().strip():
            args.extend(["--monitor-port", self.monitor_port_var.get().strip()])
        if self.model_var.get().strip():
            args.extend(["--model", self.model_var.get().strip()])
        if self.base_url_var.get().strip():
            args.extend(["--base-url", self.base_url_var.get().strip()])
        if self.api_key_var.get().strip():
            args.extend(["--api-key", self.api_key_var.get().strip()])
        if self.predict_server_var.get().strip():
            args.extend(["--predict-server-url", self.predict_server_var.get().strip()])
        if self.loop_interval_var.get().strip():
            args.extend(["--loop-interval", self.loop_interval_var.get().strip()])
        return self.fleet_call(args)

    def cmd_show_wallet(self):
        return self.fleet_call(["wallet-list"])

    def cmd_start_wallet(self):
        return self.fleet_call(["start", self.selected_wallet()])

    def cmd_stop_wallet(self):
        return self.fleet_call(["stop", self.selected_wallet()])

    def cmd_status_wallet(self):
        return self.fleet_call(["status", self.selected_wallet()])

    def cmd_balance_wallet(self):
        return self.fleet_call(["balance", self.selected_wallet()])

    def cmd_history_wallet(self):
        return self.fleet_call(["history", self.selected_wallet(), "--limit", "5"])

    def cmd_health_wallet(self):
        return self.fleet_call(["health", self.selected_wallet()])

    def cmd_health_all(self):
        return self.fleet_call(["health", "--all"])

    def cmd_wallet_list(self):
        return self.fleet_call(["wallet-list"])

    def cmd_profile_list(self):
        return self.fleet_call(["profile-list"])

    def cmd_stake(self):
        args = [
            "stake",
            self.selected_wallet(),
            "--amount",
            self.stake_amount_var.get().strip() or "1000",
            "--lock-days",
            self.lock_days_var.get().strip() or "3",
        ]
        return self.fleet_call(args)

    def show_private_key_dialog(self, wallet_name: str, private_key: str) -> None:
        win = tk.Toplevel(self)
        win.title(f"{wallet_name} Private Key")
        win.geometry("760x220")
        ttk.Label(
            win,
            text="首次创建钱包，下面是私钥。请立即保存。",
            foreground="#b00020",
            font=("SF Pro Text", 13, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 8))
        text = ScrolledText(win, height=6, wrap="word", font=("Menlo", 12))
        text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        text.insert("1.0", private_key)
        text.focus_set()


def main() -> None:
    if not FLEET_SCRIPT.exists():
        messagebox.showerror(APP_TITLE, f"Missing script: {FLEET_SCRIPT}")
        raise SystemExit(1)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
