#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import (
    JOURNAL_LOOP_UNIT,
    MONITOR_HOST as HOST,
    MONITOR_PORT as PORT,
    OPENCLAW_HOME,
    PREDICT_SERVER_URL,
    PROJECT_DIR,
    SERVICE_GATEWAY,
    SERVICE_LOOP,
    SERVICE_MONITOR,
    WALLET_BIN,
    WALLET_HOME,
    WALLET_ID,
    predict_path_prefix,
)

LOCAL_MODE = os.getenv("AWP_PREDICT_LOCAL_MODE", "").strip() == "1"
LOCAL_RUN_DIR = Path(os.getenv("AWP_PREDICT_LOCAL_RUN_DIR", "")).expanduser() if os.getenv("AWP_PREDICT_LOCAL_RUN_DIR") else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_command(args, *, timeout=60, env=None, cwd=None):
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError as exc:
        return {
            "command": args,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
        }


def parse_json_blob(blob):
    blob = (blob or "").strip()
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def wallet_export():
    env = os.environ.copy()
    env["PATH"] = f"{Path(WALLET_BIN).parent}:{env.get('PATH', '')}"
    env["HOME"] = "/root" if os.geteuid() == 0 else str(WALLET_HOME.parent)
    env["AWP_AGENT_ID"] = WALLET_ID
    env["AWP_WALLET_HOME"] = str(WALLET_HOME)
    env["AWP_WALLET_BIN"] = WALLET_BIN
    result = run_command([WALLET_BIN, "export-private-key"], timeout=30, env=env)
    parsed = parse_json_blob(result["stdout"])
    if result["returncode"] == 0 and isinstance(parsed, dict) and parsed.get("privateKey"):
        return parsed
    return {
        "error": result["stderr"] or result["stdout"] or "wallet export failed",
        "returncode": result["returncode"],
    }


def predict_env():
    exported = wallet_export()
    env = os.environ.copy()
    env["PATH"] = f"{predict_path_prefix()}:{env.get('PATH', '')}"
    env["HOME"] = str(OPENCLAW_HOME)
    env["AWP_AGENT_ID"] = WALLET_ID
    env["AWP_WALLET_HOME"] = str(WALLET_HOME)
    env["AWP_WALLET_BIN"] = WALLET_BIN
    env["PREDICT_SERVER_URL"] = PREDICT_SERVER_URL
    if exported.get("privateKey"):
        env["AWP_PRIVATE_KEY"] = exported["privateKey"]
    if exported.get("address"):
        env["AWP_ADDRESS"] = exported["address"]
    return env, exported


def run_predict_json(args, *, timeout=90):
    env, exported = predict_env()
    result = run_command(["predict-agent", *args], timeout=timeout, env=env)
    err_text = result["stderr"] or ""
    out_text = result["stdout"] or ""
    if "Signature already used" in err_text or "Signature already used" in out_text:
        time.sleep(1.5)
        result = run_command(["predict-agent", *args], timeout=timeout, env=env)
    parsed = parse_json_blob(result["stdout"])
    return {
        "result": result,
        "data": parsed if isinstance(parsed, dict) else None,
        "wallet": exported,
    }


def systemctl_summary(name: str):
    if LOCAL_MODE:
        return local_service_status(name)
    active = run_command(["systemctl", "is-active", name], timeout=10)
    enabled = run_command(["systemctl", "is-enabled", name], timeout=10)
    status = run_command(["systemctl", "status", name, "--no-pager"], timeout=15)
    return {
        "name": name,
        "active": active["stdout"] or active["stderr"] or "unknown",
        "enabled": enabled["stdout"] or enabled["stderr"] or "unknown",
        "summary": first_nonempty_line(status["stdout"]) or first_nonempty_line(status["stderr"]) or "",
    }


def service_status(name: str):
    if LOCAL_MODE:
        return local_service_status(name)
    active = run_command(["systemctl", "is-active", name], timeout=10)
    enabled = run_command(["systemctl", "is-enabled", name], timeout=10)
    status = run_command(["systemctl", "status", name, "--no-pager"], timeout=15)
    return {
        "name": name,
        "active": active["stdout"] or active["stderr"] or "unknown",
        "enabled": enabled["stdout"] or enabled["stderr"] or "unknown",
        "summary": first_nonempty_line(status["stdout"]) or first_nonempty_line(status["stderr"]) or "",
    }


def journal_tail(unit: str, lines: int = 40):
    if LOCAL_MODE:
        return local_log_tail("loop", lines)
    result = run_command(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"], timeout=20)
    text = result["stdout"] or result["stderr"]
    return [line for line in text.splitlines() if line.strip()][-lines:]


def local_pid_path(kind: str) -> Path | None:
    if not LOCAL_RUN_DIR:
        return None
    return LOCAL_RUN_DIR / f"{kind}.pid"


def local_log_path(kind: str) -> Path | None:
    if not LOCAL_RUN_DIR:
        return None
    return LOCAL_RUN_DIR / f"{kind}.log"


def local_pid_alive(kind: str) -> bool:
    path = local_pid_path(kind)
    if not path or not path.exists():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def local_service_status(name: str):
    kind = "monitor" if "monitor" in name else "loop" if "loop" in name else "gateway"
    active = "active" if (kind != "gateway" and local_pid_alive(kind)) else ("not-used-local" if kind == "gateway" else "inactive")
    return {
        "name": name,
        "active": active,
        "enabled": "local",
        "summary": f"local process ({kind})",
    }


def local_log_tail(kind: str, lines: int = 40):
    path = local_log_path(kind)
    if not path or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()][-lines:]


def first_nonempty_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def latest_openclaw_decision():
    log_dir = Path("/tmp/openclaw")
    if not log_dir.exists():
        return None
    log_files = sorted(log_dir.glob("openclaw-*.log"), reverse=True)
    for log_file in log_files[:3]:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if 'DECISION:' not in line:
                continue
            payload = line
            outer = parse_json_blob(line)
            if isinstance(outer, dict):
                raw = str(outer.get("0") or "")
                if "DECISION:" in raw:
                    payload = raw.split("DECISION:", 1)[1].strip()
                else:
                    payload = raw.strip()
            else:
                _, payload = line.split("DECISION:", 1)
                payload = payload.strip()
            try:
                decision = json.loads(payload)
            except json.JSONDecodeError:
                decision = {"raw": payload}
            return {
                "file": str(log_file),
                "decision": decision,
                "raw": payload,
            }
    return None


def latest_predict_events():
    pattern = re.compile(r"^\[predict-agent\]\s+loop:\s+")
    events = []
    for line in journal_tail(JOURNAL_LOOP_UNIT, 80):
        if LOCAL_MODE:
            clean = line.strip()
        else:
            if "predict-loop.sh[" not in line:
                continue
            clean = line.split("predict-loop.sh", 1)[-1]
            clean = clean.split("]:", 1)[-1].strip() if "]:" in clean else clean.strip()
        clean = pattern.sub("", clean)
        events.append(clean)
    return events[-20:]


def status_cards(payload):
    cards = []
    runtime = payload.get("runtime", {})
    status = runtime.get("status", {})
    orders = payload.get("orders", {})
    history = payload.get("history", {})
    context = payload.get("context", {})
    cards.append(("钱包", short_address(runtime.get("address", "-"))))
    cards.append(("筹码余额", str(status.get("balance", "-"))))
    cards.append(("人格", status.get("persona") or "-"))
    timeslot = status.get("timeslot") or {}
    cards.append(("剩余提交", f"{timeslot.get('submissions_remaining', '-')} / {timeslot.get('slot_limit', '-')}"))
    cards.append(("已用提交", str(timeslot.get("submissions_used", "-"))))
    cards.append(("开放订单", str((orders.get("summary") or {}).get("open", 0))))
    cards.append(("历史预测", str((history.get("summary") or {}).get("count", 0))))
    cards.append(("当前建议", ((context.get("recommendation") or {}).get("market_id") or "-")))
    return cards


def short_address(value: str) -> str:
    value = str(value or "-")
    if not value.startswith("0x") or len(value) < 14:
        return value
    return f"{value[:8]}...{value[-6:]}"


def dashboard_payload():
    status_info = run_predict_json(["status"], timeout=40)
    orders_info = run_predict_json(["orders"], timeout=40)
    history_info = run_predict_json(["history"], timeout=40)
    context_info = run_predict_json(["context"], timeout=60)
    return {
        "project": {
            "name": os.environ.get("AWP_PREDICT_INSTANCE_NAME", PROJECT_DIR.name),
            "projectDir": str(PROJECT_DIR),
            "serverUrl": PREDICT_SERVER_URL,
        },
        "runtime": {
            "address": (status_info.get("wallet") or {}).get("address"),
            "status": ((status_info.get("data") or {}).get("data") or {}),
            "statusMessage": (status_info.get("data") or {}).get("user_message") or status_info["result"]["stderr"],
        },
        "orders": {
            "summary": ((orders_info.get("data") or {}).get("data") or {}).get("summary") or {},
            "orders": ((orders_info.get("data") or {}).get("data") or {}).get("orders") or [],
        },
        "history": {
            "summary": ((history_info.get("data") or {}).get("data") or {}).get("summary") or {},
            "predictions": ((history_info.get("data") or {}).get("data") or {}).get("predictions") or [],
        },
        "context": {
            "recommendation": ((context_info.get("data") or {}).get("data") or {}).get("recommendation") or {},
            "markets": ((context_info.get("data") or {}).get("data") or {}).get("markets") or [],
        },
        "services": {
            "gateway": service_status(SERVICE_GATEWAY),
            "loop": service_status(SERVICE_LOOP),
            "monitor": systemctl_summary(SERVICE_MONITOR),
        },
        "latestDecision": latest_openclaw_decision(),
        "loopEvents": latest_predict_events(),
        "generatedAt": now_iso(),
    }


def render_cards(payload):
    html = []
    for label, value in status_cards(payload):
        html.append(
            f"<div class='card'><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>"
        )
    return "".join(html)


def render_orders(payload):
    orders = payload.get("orders", {}).get("orders", [])
    if not orders:
        return "<p class='muted'>当前还没有订单。</p>"
    rows = []
    for order in orders[:20]:
        rows.append(
            "<tr>"
            f"<td>{escape(str(order.get('market_id', '-')))}</td>"
            f"<td>{escape(str(order.get('prediction', order.get('direction', '-'))))}</td>"
            f"<td>{escape(str(order.get('tickets', '-')))}</td>"
            f"<td>{escape(str(order.get('status', '-')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Market</th><th>方向</th><th>筹码</th><th>状态</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_history(payload):
    items = payload.get("history", {}).get("predictions", [])
    if not items:
        return "<p class='muted'>历史预测还为空。</p>"
    rows = []
    for item in items[:20]:
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('market_id', '-')))}</td>"
            f"<td>{escape(str(item.get('prediction', item.get('direction', '-'))))}</td>"
            f"<td>{escape(str(item.get('result', '-')))}</td>"
            f"<td>{escape(str(item.get('created_at', '-')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Market</th><th>方向</th><th>结果</th><th>时间</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_markets(payload):
    markets = payload.get("context", {}).get("markets", [])
    if not markets:
        return "<p class='muted'>当前没有可提交 market。</p>"
    rows = []
    for item in markets[:12]:
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('id', '-')))}</td>"
            f"<td>{escape(str(item.get('asset', '-')))}</td>"
            f"<td>{escape(str(item.get('window', '-')))}</td>"
            f"<td>{'是' if item.get('recommended') else '否'}</td>"
            f"<td>{escape(str(item.get('closes_in_seconds', '-')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>ID</th><th>资产</th><th>窗口</th><th>推荐</th><th>剩余秒数</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_events(payload):
    events = payload.get("loopEvents", [])
    if not events:
        return "<p class='muted'>还没有 loop 日志。</p>"
    return "<pre>" + escape("\n".join(events[-18:])) + "</pre>"


def render_decision(payload):
    decision = payload.get("latestDecision")
    if not decision:
        return "<p class='muted'>还没有抓到最新决策。</p>"
    body = decision.get("decision") or {}
    return (
        "<div class='decision'>"
        f"<p><strong>Market:</strong> {escape(str(body.get('market_id', '-')))}</p>"
        f"<p><strong>方向:</strong> {escape(str(body.get('direction', '-')))}</p>"
        f"<p><strong>筹码:</strong> {escape(str(body.get('tickets', '-')))}</p>"
        f"<p><strong>限价:</strong> {escape(str(body.get('limit_price', '-')))}</p>"
        f"<p><strong>理由:</strong> {escape(str(body.get('reasoning', '-')))}</p>"
        "</div>"
    )


def render_dashboard_html(payload):
    runtime = payload.get("runtime", {})
    services = payload.get("services", {})
    generated_at = payload.get("generatedAt", "-")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>AWP Predict Monitor</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --ink: #151515;
      --muted: #6f6557;
      --panel: #fffaf2;
      --line: #dccdb6;
      --accent: #c4622d;
      --accent-2: #1f5f5b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Source Han Serif SC", "Noto Serif CJK SC", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(196, 98, 45, 0.16), transparent 26%),
        radial-gradient(circle at bottom right, rgba(31, 95, 91, 0.12), transparent 28%),
        var(--bg);
    }}
    .wrap {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 18px 56px;
    }}
    .hero {{
      padding: 22px;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(255,250,242,0.96), rgba(245,236,220,0.92));
      border-radius: 22px;
      margin-bottom: 20px;
    }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ margin: 0; line-height: 1.6; }}
    .muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 16px 0 20px;
    }}
    .card, .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(21,21,21,0.04);
    }}
    .card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .card strong {{
      font-size: 20px;
    }}
    .sections {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 16px;
    }}
    .stack {{
      display: grid;
      gap: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 10px 6px;
      vertical-align: top;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      line-height: 1.5;
      color: #21302d;
    }}
    .service {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
    }}
    .service:last-child {{ border-bottom: 0; }}
    .pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 4px 10px;
      background: rgba(31,95,91,0.12);
      color: var(--accent-2);
      font-size: 12px;
      margin-left: 8px;
    }}
    .decision p {{ margin: 0 0 10px; }}
    .footer {{ margin-top: 14px; color: var(--muted); font-size: 13px; }}
    @media (max-width: 900px) {{
      .sections {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Predict WorkNet Monitor</h1>
      <p>{escape(runtime.get("statusMessage") or "预测服务运行中")}</p>
      <div class="grid">{render_cards(payload)}</div>
      <p class="muted">生成时间：{escape(str(generated_at))}<span class="pill">persona: {escape(str((runtime.get('status') or {}).get('persona') or '-'))}</span><span class="pill">15s 自动刷新</span></p>
    </section>
    <div class="sections">
      <div class="stack">
        <section class="panel">
          <h2>最新决策</h2>
          {render_decision(payload)}
        </section>
        <section class="panel">
          <h2>可提交 Market</h2>
          {render_markets(payload)}
        </section>
        <section class="panel">
          <h2>历史预测</h2>
          {render_history(payload)}
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2>服务状态</h2>
          <div class="service"><div>Gateway</div><div>{escape(str((services.get('gateway') or {}).get('active', '-')))} / {escape(str((services.get('gateway') or {}).get('enabled', '-')))}</div></div>
          <div class="service"><div>Loop</div><div>{escape(str((services.get('loop') or {}).get('active', '-')))} / {escape(str((services.get('loop') or {}).get('enabled', '-')))}</div></div>
        </section>
        <section class="panel">
          <h2>当前订单</h2>
          {render_orders(payload)}
        </section>
        <section class="panel">
          <h2>Loop 日志</h2>
          {render_events(payload)}
          <div class="footer">接口：<a href="./api/dashboard">./api/dashboard</a> · 健康检查：<a href="./health">./health</a></div>
        </section>
      </div>
    </div>
  </div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_common_headers(self, content_type: str, content_length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self._send_common_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "dashboard")
            self.end_headers()
            return
        if path == "/health":
            return self.send_json(200, {"ok": True, "generatedAt": now_iso()})
        if path == "/api/dashboard":
            return self.send_json(200, dashboard_payload())
        if path == "/dashboard":
            payload = dashboard_payload()
            body = render_dashboard_html(payload).encode("utf-8")
            self.send_response(200)
            self._send_common_headers("text/html; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(json.dumps({"host": HOST, "port": PORT, "service": SERVICE_MONITOR}, ensure_ascii=False))
    server.serve_forever()


if __name__ == "__main__":
    main()
