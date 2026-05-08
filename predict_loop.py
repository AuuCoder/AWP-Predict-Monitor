#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import request, error

from config import (
    DEFAULT_BASE_URL,
    DEFAULT_INTERVAL,
    DEFAULT_MODEL,
    MINER_ENV,
    OPENCLAW_HOME,
    PREDICT_SERVER_URL,
    WALLET_BIN,
    WALLET_HOME,
    WALLET_ID,
    predict_path_prefix,
    wallet_path_prefix,
)

STAKE_GATE_TEXT = "has not allocated any AWP to the Predict WorkNet"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def log(message: str) -> None:
    print(f"[predict-loop] {message}", flush=True)


def notify(message: str) -> None:
    print(f"[NOTIFY] {message}", flush=True)


def run_command(args, *, env=None, timeout=90):
    completed = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed


def parse_json_blob(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except json.JSONDecodeError:
            continue
    return None


def wallet_env():
    env = os.environ.copy()
    env["PATH"] = f"{wallet_path_prefix()}:{env.get('PATH', '')}"
    env["HOME"] = "/root" if os.geteuid() == 0 else str(WALLET_HOME.parent)
    env["AWP_AGENT_ID"] = WALLET_ID
    env["AWP_WALLET_HOME"] = str(WALLET_HOME)
    env["AWP_WALLET_BIN"] = WALLET_BIN
    return env


def predict_env():
    env = os.environ.copy()
    env["PATH"] = f"{predict_path_prefix()}:{env.get('PATH', '')}"
    env["HOME"] = str(OPENCLAW_HOME)
    env["AWP_AGENT_ID"] = WALLET_ID
    env["AWP_WALLET_HOME"] = str(WALLET_HOME)
    env["AWP_WALLET_BIN"] = WALLET_BIN
    env["PREDICT_SERVER_URL"] = PREDICT_SERVER_URL
    return env


def export_wallet():
    result = run_command([WALLET_BIN, "export-private-key"], env=wallet_env(), timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "wallet export failed")
    payload = json.loads(result.stdout)
    return payload["privateKey"], payload["address"]


def call_predict_json(args, *, timeout=90):
    private_key, address = export_wallet()
    env = predict_env()
    env["AWP_PRIVATE_KEY"] = private_key
    env["AWP_ADDRESS"] = address
    result = run_command(["predict-agent", *args], env=env, timeout=timeout)
    payload = parse_json_blob(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "predict-agent failed")
    if not isinstance(payload, dict):
        raise RuntimeError(f"predict-agent returned non-JSON output for {' '.join(args)}")
    return payload


def call_model(prompt: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("MINE_GATEWAY_TOKEN") or ""
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("MINE_GATEWAY_BASE_URL") or DEFAULT_BASE_URL
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("MINE_ENRICH_MODEL") or DEFAULT_MODEL
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a disciplined prediction assistant. "
                    "Return ONLY one JSON object and nothing else."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1600,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
        raise RuntimeError(f"model HTTP {exc.code}: {body[:500]}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = parse_json_blob(content)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"model did not return JSON: {content[:400]}")
    return parsed


def build_prompt(context_payload: dict, challenge_payload: dict) -> str:
    data = context_payload["data"]
    agent = data["agent"]
    recommendation = data["recommendation"]
    market_id = recommendation["market_id"]
    market = next((m for m in data["markets"] if m["id"] == market_id), data["markets"][0])
    candles = data["klines"]["candles"][-30:]
    challenge = challenge_payload["data"]
    return f"""
You are trading AWP Predict WorkNet. Return ONLY JSON.

Goal:
- maximize long-run chip balance
- use remaining submissions efficiently
- reason from actual market structure, not boilerplate

Current state:
- balance: {agent.get('balance')}
- persona: {agent.get('persona')}
- submissions_remaining: {(agent.get('timeslot') or {}).get('submissions_remaining')}
- submissions_used: {(agent.get('timeslot') or {}).get('submissions_used')}
- recommended_market: {market_id}

Selected market:
{json.dumps(market, ensure_ascii=False)}

Recent klines (last 30 x 1m):
{json.dumps(candles, ensure_ascii=False)}

Challenge:
- question: {challenge.get('challenge')}
- instructions: {challenge.get('instructions')}

Respond with one JSON object:
{{
  "action": "submit" or "skip",
  "direction": "up" or "down",
  "tickets": integer,
  "limit_price": number or null,
  "reasoning": "2-6 sentences, concrete market analysis in English",
  "challenge_answer": integer
}}

Rules:
- If action is submit, use market_id exactly: {market_id}
- reasoning must be in English
- reasoning must mention concrete market behavior from the provided candles
- reasoning must be at least 2 complete sentences
- reasoning should usually be 80-400 English characters
- avoid generic filler unless tied to actual price/volume structure
- choose tickets between 1500 and 2500
- challenge_answer must be the numeric answer to the challenge question
- do not include markdown
""".strip()


def normalize_decision(decision: dict, market_id: str) -> dict:
    action = str(decision.get("action") or "submit").strip().lower()
    if action not in {"submit", "skip"}:
        action = "submit"
    direction = str(decision.get("direction") or "down").strip().lower()
    if direction not in {"up", "down"}:
        direction = "down"
    try:
        tickets = int(decision.get("tickets") or 2000)
    except Exception:
        tickets = 2000
    tickets = max(100, min(10000, tickets))
    limit_price = decision.get("limit_price")
    try:
        if limit_price in ("", None):
            limit_price = None
        else:
            limit_price = round(float(limit_price), 2)
            if not (0.01 <= limit_price <= 0.99):
                limit_price = None
    except Exception:
        limit_price = None
    reasoning = str(decision.get("reasoning") or "").strip()
    answer = decision.get("challenge_answer")
    try:
        answer = int(answer)
    except Exception:
        answer = None
    return {
        "action": action,
        "direction": direction,
        "tickets": tickets,
        "limit_price": limit_price,
        "reasoning": reasoning,
        "challenge_answer": answer,
        "market_id": market_id,
    }


def validate_reasoning(reasoning: str) -> None:
    if not reasoning:
        raise RuntimeError("empty reasoning")
    if len(reasoning) < 80:
        raise RuntimeError("reasoning too short")
    sentence_count = sum(reasoning.count(mark) for mark in [".", "!", "?"])
    if sentence_count < 2:
        raise RuntimeError("reasoning must contain at least 2 sentences")
    lowered = reasoning.lower()
    keyword_hits = sum(
        kw in lowered
        for kw in [
            "candle",
            "close",
            "volume",
            "high",
            "low",
            "range",
            "bounce",
            "rebound",
            "selloff",
            "breakout",
            "trend",
            "higher low",
            "lower high",
        ]
    )
    if keyword_hits < 2:
        raise RuntimeError("reasoning is too generic")


def submit_prediction(decision: dict, challenge_nonce: str):
    reasoning = decision["reasoning"].rstrip()
    validate_reasoning(reasoning)
    if decision["challenge_answer"] is None:
        raise RuntimeError("missing challenge answer")
    reasoning = f"{reasoning}\nChallenge: {decision['challenge_answer']}"
    args = [
        "submit",
        "--market",
        decision["market_id"],
        "--prediction",
        decision["direction"],
        "--tickets",
        str(decision["tickets"]),
        "--reasoning",
        reasoning,
        "--challenge-nonce",
        challenge_nonce,
    ]
    if decision["limit_price"] is not None:
        args.extend(["--limit-price", str(decision["limit_price"])])
    return call_predict_json(args, timeout=90)


def extract_submit_message(result: dict) -> str:
    return (
        result.get("user_message")
        or result.get("message")
        or result.get("error", "")
        or result.get("status", "")
        or "submitted"
    )


def loop_forever():
    interval = int(os.environ.get("PREDICT_LOOP_INTERVAL", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)
    iteration = 0
    while True:
        iteration += 1
        try:
            log(f"iteration {iteration}: fetching context")
            context_payload = call_predict_json(["context"], timeout=60)
            data = context_payload.get("data") or {}
            recommendation = data.get("recommendation") or {}
            market_id = recommendation.get("market_id")
            if recommendation.get("action") != "submit" or not market_id:
                reason = recommendation.get("reason") or "no submittable markets"
                log(f"iteration {iteration}: {reason}")
                notify(f"Predict round {iteration}: {reason}")
                time.sleep(interval)
                continue

            markets = data.get("markets") or []
            if any(m.get("id") == market_id and m.get("already_submitted") for m in markets):
                reason = f"{market_id} already submitted in this window"
                log(f"iteration {iteration}: {reason}")
                notify(f"Predict round {iteration}: {reason}")
                time.sleep(interval)
                continue

            log(f"iteration {iteration}: fetching challenge for {market_id}")
            challenge_payload = call_predict_json(["challenge", "--market", market_id], timeout=30)
            challenge_data = challenge_payload.get("data") or {}
            nonce = challenge_data.get("nonce")
            if not nonce:
                raise RuntimeError("challenge nonce missing")

            prompt = build_prompt(context_payload, challenge_payload)
            log(f"iteration {iteration}: calling model for {market_id}")
            raw_decision = call_model(prompt)
            decision = normalize_decision(raw_decision, market_id)
            if decision["action"] != "submit":
                reason = raw_decision.get("reasoning") or "model chose to skip"
                log(f"iteration {iteration}: skipped {market_id}: {reason}")
                notify(f"Predict round {iteration}: skipped {market_id}")
                time.sleep(interval)
                continue

            log(
                f"iteration {iteration}: submitting {decision['direction']} {decision['tickets']} "
                f"tickets for {market_id} @ {decision['limit_price']}"
            )
            result = submit_prediction(decision, nonce)
            msg = extract_submit_message(result)
            if "Submission failed:" in msg or STAKE_GATE_TEXT in msg:
                raise RuntimeError(msg)
            log(f"iteration {iteration}: submit ok: {msg}")
            notify(f"Predict round {iteration}: submitted {decision['direction']} on {market_id}")
            time.sleep(interval)
        except Exception as exc:
            log(f"iteration {iteration}: error: {exc}")
            notify(f"Predict round {iteration}: error {exc}")
            backoff = min(240, max(30, interval))
            if STAKE_GATE_TEXT in str(exc):
                backoff = max(backoff, 180)
            time.sleep(backoff)


def main():
    load_env_file(MINER_ENV)
    log("custom predict loop starting")
    loop_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
