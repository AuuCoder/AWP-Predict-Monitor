---
name: awp-wallet
description: >
  Self-custodial EVM blockchain wallet for executing on-chain operations.
  Use this skill whenever the user wants to: send or transfer crypto/tokens
  (ETH, USDC, USDT, etc.) to an address, check wallet balance or portfolio,
  get their wallet/receiving address, approve or revoke token spending
  allowances, sign messages or typed data, estimate gas costs, check
  transaction status, view transaction history, or do batch transfers.
  Supports 400+ EVM chains (Ethereum, Base, Arbitrum, Polygon, BSC, etc.)
  with automatic gasless fallback. This is the skill to use for ANY
  on-chain wallet operation — if the user mentions sending tokens,
  checking balances, wallet addresses, token approvals, or signing,
  use this skill even if they do not explicitly say "wallet".
metadata:
  openclaw:
    requires:
      bins:
        - node
        - git
        - openssl
      anyBins:
        - npm
    emoji: "\U0001F4B0"
    homepage: https://github.com/awp-core/awp-wallet
    install:
      - kind: node
        package: awp-wallet
        bins: [awp-wallet]
---

# AWP Wallet

EVM wallet for AI agents. All output is JSON. No passwords, no session tokens needed.

## Setup

```bash
awp-wallet setup
```

Creates wallet if needed. Returns `{ "status": "ready", "address": "0x..." }`.

### If awp-wallet is not installed

```bash
git clone https://github.com/awp-core/awp-wallet.git ~/awp-wallet && cd ~/awp-wallet && bash install.sh
```

If `command not found` after install: `export PATH="$HOME/.local/bin:$PATH"`

## Commands

No `--token` needed. Just run the command directly.

### Balance / Portfolio
```bash
awp-wallet balance --chain ethereum
awp-wallet balance --chain base --asset usdc
awp-wallet portfolio
```

### Send
Confirm with user first:
```
[TX] about to send:
     to:      0xBob...1234
     amount:  50 USDC
     chain:   Base
     proceed? (y/n)
```
```bash
awp-wallet send --to 0xAddr --amount 0.1 --chain ethereum
awp-wallet send --to 0xAddr --amount 100 --asset usdc --chain base
```

### Receive
```bash
awp-wallet receive
```

### Approve / Revoke
```bash
awp-wallet approve --asset usdc --spender 0xRouter --amount 1000 --chain base
awp-wallet revoke --asset usdc --spender 0xRouter --chain base
```

### Sign
```bash
awp-wallet sign-message --message "Hello World"
awp-wallet sign-typed-data --data '{"types":{...},...}'
```

### Gas Estimate
```bash
awp-wallet estimate --to 0xAddr --amount 0.1 --chain ethereum
```

### Transaction Status
```bash
awp-wallet tx-status --hash 0xHash --chain ethereum
```

### History
```bash
awp-wallet history --chain ethereum --limit 20
```

### Batch Send
```bash
awp-wallet batch --chain base \
  --ops '[{"to":"0xA","amount":"10","asset":"usdc"},{"to":"0xB","amount":"20","asset":"usdc"}]'
```

### Raw Transaction (call any contract without touching private key)
```bash
# Sign only (returns signed hex, caller broadcasts)
awp-wallet sign-tx --to 0xContract --data 0xabcdef... --chain base
awp-wallet sign-tx --to 0xContract --data 0xabcdef... --value 1000000000000000 --chain base

# Sign + broadcast (awp-wallet handles everything)
awp-wallet send-tx --to 0xContract --data 0xabcdef... --chain base
awp-wallet send-tx --to 0xContract --data 0xabcdef... --gas 200000 --chain base
```

Use `sign-tx` / `send-tx` when you need to call arbitrary contract methods (DeFi, NFT, staking, etc.). Build your calldata externally, pass it via `--data`.

### Export (for wallet migration)
```bash
awp-wallet export                  # mnemonic
awp-wallet export-private-key      # private key
```

## Chains

`--chain` name or ID. Default: `ethereum`.

16 built-in: `ethereum` `base` `bsc` `arbitrum` `optimism` `polygon` `avalanche` `fantom` `zksync` `linea` `scroll` `mantle` `blast` `celo` `sepolia` `base-sepolia`

Custom: `--chain 99999 --rpc-url https://custom.rpc.com`

"on Base" → `--chain base`. "on BSC" / "BNB Chain" → `--chain bsc`. No chain mentioned → `ethereum`.

## Assets

`--asset` symbol or `0x...` address. Omit for native (ETH, BNB, etc.).

Built-in: `usdc` `usdt` `awp` `weth` `wbnb` `dai`

## Output Tags

| Tag | When |
|-----|------|
| `[QUERY]` | Balance, gas estimates |
| `[TX]` | Transactions — include explorer link |
| `[SIGN]` | Signing |
| `[WALLET]` | Wallet info |

## Error Recovery

| Error | Fix |
|-------|-----|
| `command not found` | Install (see Setup) |
| `No wallet found` | `awp-wallet setup` |
| `Insufficient balance` | Tell user; suggest `--mode gasless` |

## Advanced

```bash
awp-wallet chains                                       # list all chains
awp-wallet chain-info --chain zksync                    # chain details
awp-wallet wallets                                      # list wallet profiles
awp-wallet wallet-id                                    # current profile ID
awp-wallet status                                       # wallet address
awp-wallet allowances --asset usdc --spender 0xRouter --chain base
awp-wallet verify-log                                   # audit log integrity
awp-wallet upgrade-7702 --chain ethereum                # EIP-7702 upgrade
awp-wallet revoke-7702 --chain ethereum                 # revoke EIP-7702
awp-wallet deploy-4337 --chain ethereum                 # smart account status
```

## Environment Variables (all optional)

| Variable | Purpose |
|----------|---------|
| `PIMLICO_API_KEY` | Enable gasless ERC-4337 transactions |
| `AWP_AGENT_ID` | Multi-agent wallet isolation |
| `AWP_SESSION_ID` | Per-session wallet isolation |

Gasless auto-activates when no native gas and `PIMLICO_API_KEY` is set. Force: `--mode gasless`.
