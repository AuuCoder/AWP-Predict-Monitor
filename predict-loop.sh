#!/bin/sh
set -eu

PROJECT_DIR="${AWP_PREDICT_PROJECT_DIR:-/srv/awp-predict}"
OPENCLAW_HOME="${AWP_PREDICT_OPENCLAW_HOME:-$PROJECT_DIR}"
WALLET_BIN="${AWP_WALLET_BIN:-/srv/awp-miner/vendor/node/bin/awp-wallet}"
WALLET_HOME="${AWP_WALLET_HOME:-/srv/awp-miner/.wallet}"
AWP_AGENT_ID="${AWP_AGENT_ID:-awp-miner}"
PREDICT_SERVER_URL="${PREDICT_SERVER_URL:-https://api.agentpredict.work}"
PATH_PREFIX="$PROJECT_DIR:$(dirname "$WALLET_BIN"):$OPENCLAW_HOME/.openclaw/bin"

export PATH="$PATH_PREFIX:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export HOME="$OPENCLAW_HOME"
export AWP_AGENT_ID
export AWP_WALLET_HOME="$WALLET_HOME"
export AWP_WALLET_BIN="$WALLET_BIN"
export PREDICT_SERVER_URL

wallet_json=$(HOME="/root" "$WALLET_BIN" export-private-key)
AWP_PRIVATE_KEY=$(printf '%s' "$wallet_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["privateKey"])')
AWP_ADDRESS=$(printf '%s' "$wallet_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["address"])')

export AWP_PRIVATE_KEY
export AWP_ADDRESS

exec /usr/bin/python3 "$PROJECT_DIR/predict_loop.py"
