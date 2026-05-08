# AWP Wallet

  <p align="center">
    <a href="https://awp.pro/">
      <img src="assets/banner.png" alt="AWP - Agent Work Protocol" width="800">
    </a>
  </p>

  <p align="center">
    <img src="https://img.shields.io/badge/EVM-400%2B_Chains-6C47FF?style=flat" alt="EVM">
    <img src="https://img.shields.io/badge/ERC--4337-Gasless-F0B90B?style=flat" alt="ERC-4337">
    <img src="https://img.shields.io/badge/Self--Custodial-16A34A?style=flat&logo=ethereum&logoColor=white" alt="Self-Custodial">
    <img src="https://img.shields.io/badge/28_CLI_Commands-1a1a1a?style=flat&logo=windowsterminal&logoColor=white" alt="CLI">
    <img src="https://img.shields.io/badge/License-MIT-97CA00?style=flat" alt="MIT">
  </p>

  Self-custodial, chain-agnostic EVM blockchain wallet for AI agents. Direct EOA transactions by default, with on-demand ERC-4337 gasless support. No password needed — auto-managed encryption.

  ### Works with

  <p align="center">
    <a href="https://github.com/anthropics/claude-code"><img src="https://img.shields.io/badge/Claude_Code-191919?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>
    &nbsp;
    <a href="https://github.com/openclaw/openclaw"><img src="https://img.shields.io/badge/OpenClaw-FF4500?style=for-the-badge" alt="OpenClaw"></a>
    &nbsp;
    <a href="https://cursor.sh"><img src="https://img.shields.io/badge/Cursor-000000?style=for-the-badge" alt="Cursor"></a>
    &nbsp;
    <a href="https://openai.com/codex"><img src="https://img.shields.io/badge/Codex-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>
    &nbsp;
    <a href="https://ai.google.dev/gemini-api/docs/cli"><img src="https://img.shields.io/badge/Gemini_CLI-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini CLI"></a>
    &nbsp;
    <a href="https://windsurf.ai"><img src="https://img.shields.io/badge/Windsurf-06B6D4?style=for-the-badge" alt="Windsurf"></a>
  </p>

  <p align="center">Any agent that can invoke CLI commands.</p>

  ---

  ## Install

  ```bash
  git clone https://github.com/awp-core/awp-wallet.git
  cd awp-wallet && bash install.sh
  ```

  `install.sh` does everything: installs deps, registers the CLI, creates wallet, verifies.

  **Options:**

  | Flag | Default | Description |
  |------|---------|-------------|
  | `--no-init` | Init enabled | Install only, skip wallet creation |
  | `--mnemonic <phrase>` | New wallet | Import existing wallet |
  | `--password <pwd>` | Auto-managed | Explicit password mode |
  | `--agent-id <id>` | `default` | Multi-agent isolation |
  | `--session-id <id>` | — | Per-session isolation |
  | `--pimlico <key>` | None | Enable gasless transactions |
  | `--dir <path>` | `~/awp-wallet` | Installation directory |

  ## How It Works

  ```
  Agent
    │
    │  User: "Send 50 USDC to 0xBob on Base"
    │
    ├─ 1. awp-wallet unlock --duration 300
    │     → { "sessionToken": "wlt_abc..." }
    │
    ├─ 2. awp-wallet send --token wlt_abc --to 0xBob --amount 50 --asset usdc --chain base
    │     → { "status": "sent", "txHash": "0x...", "mode": "direct" }
    │
    └─ 3. awp-wallet lock
          → { "status": "locked" }
  ```

  Each command outputs JSON. The agent only sees session tokens — **never** private keys. No password needed.

  ## Features

  - **400+ EVM chains** — 16 preconfigured + any custom chain
  - **Dual-mode** — Direct EOA (default) or gasless ERC-4337 (auto when no gas)
  - **Self-custodial** — Private keys never leave the wallet process
  - **Auto-managed** — No password configuration needed
  - **Multi-agent** — Per-agent or per-session wallet isolation
  - **16 chains** — Ethereum, Base, BSC, Arbitrum, Optimism, Polygon, Avalanche, Fantom, zkSync, Linea, Scroll, Mantle, Blast, Celo + testnets
  - **28 commands** — Send, balance, approve, revoke, sign, estimate, batch, and more

  ## Commands

  | Command | What It Does |
  |---------|-------------|
  | `init` | Create a new wallet |
  | `import --mnemonic "..."` | Import from seed phrase |
  | `unlock / lock` | Session management |
  | `balance / portfolio` | Check balances |
  | `send / batch` | Transfer tokens |
  | `approve / revoke` | Token approvals |
  | `estimate` | Gas estimation |
  | `sign-message / sign-typed-data` | Signing (EIP-191/712) |
  | `history / tx-status / verify-log` | Transaction tracking |
  | `chain-info / chains / receive` | Chain & address info |
  | `wallets / wallet-id` | Multi-agent profiles |
  | `change-password / export` | Account management |
  | `upgrade-7702 / deploy-4337 / revoke-7702` | Smart account ops |

  See [SKILL.md](SKILL.md) for full command reference.

  ## Architecture

  ```
  Each command = independent Node.js process
    ├── Reads encrypted keystore (scrypt N=262144)
    ├── Decrypts signer from AES-GCM cache (scrypt N=16384)
    ├── Executes on-chain operation via viem
    ├── Returns JSON result
    └── Process exits — all secrets destroyed
  ```

  ## Security

  | Layer | Protection |
  |-------|-----------|
  | Keystore | scrypt (N=262144) + AES-128-CTR |
  | Signer cache | scrypt (N=16384) + AES-256-GCM |
  | Session tokens | HMAC-SHA256, time-limited, tamper-proof |
  | File permissions | 0o600/0o700 (owner-only) |
  | Process isolation | Keys destroyed on exit |
  | Transaction limits | Per-tx and 24h rolling caps |
  | Audit log | SHA-256 hash-chain |

  **Private keys never enter the agent's context.**

  ## Environment Variables

  All optional — the wallet works with zero configuration.

  | Variable | Purpose |
  |----------|---------|
  | `WALLET_PASSWORD` | Explicit password (default: auto-managed) |
  | `PIMLICO_API_KEY` | Enable gasless ERC-4337 |
  | `AWP_AGENT_ID` | Multi-agent wallet isolation |
  | `AWP_SESSION_ID` | Per-session wallet isolation |

  ## Platform Integration

  ### Claude Code

  ```bash
  cat awp-wallet/docs/CLAUDE-WEB3-GUIDE.md >> your-project/CLAUDE.md
  ```

  See [docs/CLAUDE-WEB3-GUIDE.md](docs/CLAUDE-WEB3-GUIDE.md).

  ### Other Agents

  Works with any agent that can run CLI commands and parse JSON. Point your agent to `SKILL.md`.

  ## Tech Stack

  | Layer | Technology |
  |-------|-----------|
  | CLI | commander |
  | Keystore | ethers v6 + AES-256-GCM cache |
  | Transactions | viem (direct EOA) |
  | Smart Accounts | permissionless 0.3 (Kernel v3) |
  | Bundler | viem/account-abstraction |
  | Chain Registry | viem/chains (400+) |

  4 runtime dependencies. Node.js >= 20.

  ## Quick Start

  ```bash
  awp-wallet init
  TOKEN=$(awp-wallet unlock --duration 31536000 | jq -r '.sessionToken')
  awp-wallet balance --token $TOKEN --chain ethereum
  awp-wallet send --token $TOKEN --to 0xRecipient --amount 50 --asset usdc --chain base
  awp-wallet lock
  ```

  ## Development

  ```bash
  # Tests
  node --test tests/integration/*.test.js tests/e2e/*.test.js

  # Update
  cd awp-wallet && git pull && npm install
  ```

  ## License

  [MIT](LICENSE)
