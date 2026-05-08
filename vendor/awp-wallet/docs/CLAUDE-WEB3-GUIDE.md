# AWP Wallet — Claude Code Integration Guide

> Add the relevant sections to your web3 project's CLAUDE.md so Claude Code knows how to use the wallet.

## Add to Your Project's CLAUDE.md

````markdown
## Local Wallet (awp-wallet)

This project uses `awp-wallet` as the local EVM wallet. No password needed — the wallet auto-manages encryption.

### Install (once)

```bash
git clone https://github.com/awp-core/awp-wallet.git /tmp/awp-wallet
cd /tmp/awp-wallet && bash install.sh
```

### Session Workflow

```bash
# Ensure wallet exists
awp-wallet receive 2>/dev/null || awp-wallet init

# Unlock (get session token)
TOKEN=$(awp-wallet unlock --duration 31536000 | jq -r '.sessionToken')

# Use token for operations
awp-wallet balance --token $TOKEN --chain ethereum
awp-wallet send --token $TOKEN --to 0x... --amount 0.1 --chain ethereum

# Lock when done
awp-wallet lock
```

In Node.js:

```javascript
import { execFileSync } from "node:child_process"

function wallet(args) {
  const result = execFileSync("awp-wallet", args, {
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  })
  return JSON.parse(result)
}

// Unlock
const { sessionToken } = wallet(["unlock", "--duration", "3600"])

// Balance
const bal = wallet(["balance", "--token", sessionToken, "--chain", "ethereum"])

// Send
const tx = wallet(["send", "--token", sessionToken, "--to", "0x...", "--amount", "0.1", "--chain", "ethereum"])

// Lock
wallet(["lock"])
```

### Chain Selection

```bash
--chain ethereum / --chain base / --chain bsc / --chain arbitrum / --chain polygon
--chain avalanche / --chain fantom / --chain zksync / --chain linea / --chain scroll
--chain mantle / --chain blast / --chain celo / --chain optimism
--chain 99999 --rpc-url https://custom-rpc.com   # any EVM chain
```

Default chain: `ethereum`. 16 preconfigured + 400+ via viem.

### Token Selection

```bash
--asset usdc / --asset usdt / --asset awp / --asset weth / --asset wbnb / --asset dai
--asset 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913  # any token by address
```

### Common Operations

```bash
# Balance
awp-wallet balance --token $T --chain ethereum
awp-wallet balance --token $T --chain bsc --asset usdc
awp-wallet portfolio --token $T

# Send
awp-wallet send --token $T --to 0xAddr --amount 0.1 --chain ethereum
awp-wallet send --token $T --to 0xAddr --amount 100 --asset usdc --chain base
awp-wallet send --token $T --to 0xAddr --amount 50 --asset usdc --chain base --mode gasless

# Approve / Revoke
awp-wallet approve --token $T --asset usdc --spender 0xRouter --amount 1000 --chain base
awp-wallet revoke --token $T --asset usdc --spender 0xRouter --chain base

# Sign
awp-wallet sign-message --token $T --message "Hello World"
awp-wallet sign-typed-data --token $T --data '{"types":{...},"primaryType":"...","domain":{...},"message":{...}}'

# Batch
awp-wallet batch --token $T --chain base --ops '[{"to":"0xA","amount":"10","asset":"usdc"}]'

# Gas estimate
awp-wallet estimate --to 0xAddr --amount 0.1 --chain ethereum

# Address
awp-wallet receive

# Transaction status
awp-wallet tx-status --hash 0xHash --chain ethereum

# History
awp-wallet history --token $T --chain ethereum --limit 20

# Allowances
awp-wallet allowances --token $T --asset usdc --spender 0xRouter --chain base
```

### Contract Deployment

The wallet doesn't deploy contracts directly. Export the mnemonic for your deployment framework:

```javascript
// Requires explicit WALLET_PASSWORD (auto-managed wallets cannot export)
const exported = wallet(["export"], {
  env: { ...process.env, WALLET_PASSWORD: "your-password" }
})
// Use exported.mnemonic with Hardhat, Forge, etc.
```

### Transaction Limits

Configurable in `~/.openclaw-wallet/wallets/default/config.json`:

```
Per-transaction: USDC 500, ETH 0.25, default 250
Daily (24h):     USDC 1000, ETH 0.5, BNB 1.0, default 500
```

### Error Handling

```javascript
function wallet(args) {
  try {
    return { ok: true, data: JSON.parse(execFileSync("awp-wallet", args, { encoding: "utf8", stdio: ["pipe","pipe","pipe"] })) }
  } catch (e) {
    try { return { ok: false, error: JSON.parse((e.stderr||"").trim()).error } }
    catch { return { ok: false, error: e.message } }
  }
}
```

### Environment Variables (all optional)

| Variable | Purpose |
|----------|---------|
| `WALLET_PASSWORD` | Explicit password mode (default: auto-managed) |
| `PIMLICO_API_KEY` | Enable gasless ERC-4337 transactions |
| `AWP_AGENT_ID` | Multi-agent wallet isolation |

### Gasless Mode

Auto-activates when no native gas. Requires `PIMLICO_API_KEY`. Force with `--mode gasless`.
````

## Wallet Helper Script

For complex web3 projects, create a `scripts/wallet.js` helper:

```javascript
// scripts/wallet.js — AWP Wallet helper
import { execFileSync } from "node:child_process"

function call(args) {
  try {
    return JSON.parse(execFileSync("awp-wallet", args, {
      encoding: "utf8", stdio: ["pipe", "pipe", "pipe"], timeout: 120_000,
    }))
  } catch (e) {
    const msg = (e.stderr || e.stdout || "").trim()
    try { throw new Error(JSON.parse(msg).error) }
    catch (inner) { if (inner.message !== msg) throw inner; throw new Error(msg || e.message) }
  }
}

let _token = null

export function unlock(duration = 3600) {
  const { sessionToken } = call(["unlock", "--duration", String(duration)])
  _token = sessionToken
  return sessionToken
}

export function lock() { _token = null; return call(["lock"]) }

export function token() {
  if (!_token) throw new Error("Call unlock() first.")
  return _token
}

export const balance = (chain, asset) => {
  const args = ["balance", "--token", token(), "--chain", chain]
  if (asset) args.push("--asset", asset)
  return call(args)
}

export const send = ({ to, amount, chain, asset, mode }) => {
  const args = ["send", "--token", token(), "--to", to, "--amount", String(amount), "--chain", chain]
  if (asset) args.push("--asset", asset)
  if (mode) args.push("--mode", mode)
  return call(args)
}

export const approve = ({ asset, spender, amount, chain }) =>
  call(["approve", "--token", token(), "--asset", asset, "--spender", spender, "--amount", String(amount), "--chain", chain])

export const revoke = ({ asset, spender, chain }) =>
  call(["revoke", "--token", token(), "--asset", asset, "--spender", spender, "--chain", chain])

export const signMessage = (message) =>
  call(["sign-message", "--token", token(), "--message", message])

export const signTypedData = (data) =>
  call(["sign-typed-data", "--token", token(), "--data", JSON.stringify(data)])

export const address = (chain) => {
  const args = ["receive"]
  if (chain) args.push("--chain", chain)
  return call(args)
}

export const estimate = ({ to, amount, chain, asset }) => {
  const args = ["estimate", "--to", to, "--amount", String(amount), "--chain", chain]
  if (asset) args.push("--asset", asset)
  return call(args)
}

export const txStatus = (hash, chain) =>
  call(["tx-status", "--hash", hash, "--chain", chain])
```

Usage:

```javascript
import { unlock, balance, send, lock, address } from "./scripts/wallet.js"

const { eoaAddress } = address("ethereum")
console.log("Wallet:", eoaAddress)

unlock(3600)
const bal = balance("ethereum", "usdc")
console.log("USDC:", bal.balances.USDC)

const tx = send({ to: "0xRecipient", amount: "50", chain: "base", asset: "usdc" })
console.log("TX:", tx.txHash)

lock()
```
