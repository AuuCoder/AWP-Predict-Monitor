/**
 * tx-validator.js integration tests
 * Tests transaction validation: address checking, limit checking, allowlist mode
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import {
  createTestEnv, runCli, initAndUnlock, readWalletFile, PROJECT_ROOT,
} from "../helpers/setup.js"

/**
 * Execute validation functions in an isolated environment, returns JSON result
 * On success returns { ok: true, ... }, on failure returns { error: message }
 */
function execValidator(home, code) {
  const script = `
    process.env.HOME = ${JSON.stringify(home)};
    ${code}
  `
  const out = execFileSync("node", ["--input-type=module", "-e", script], {
    env: { ...process.env, HOME: home, WALLET_PASSWORD: "test-pwd-123" },
    cwd: PROJECT_ROOT,
    encoding: "utf8",
    timeout: 30_000,
  }).trim()
  return out ? JSON.parse(out) : null
}

/**
 * A valid test address (non-zero, not the wallet's own address)
 */
const VALID_RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
const INVALID_ADDR = "0xinvalid"
const ZERO_ADDR = "0x0000000000000000000000000000000000000000"

describe("tx-validator", () => {
  let ctx

  beforeEach(() => {
    ctx = initAndUnlock()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  // ---- validateTransaction: address validation ----

  it("validateTransaction: valid address passes validation", () => {
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "1", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.equal(result.ok, true)
  })

  it("validateTransaction: invalid address throws 'Invalid Ethereum address'", () => {
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${INVALID_ADDR}", amount: "1", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Invalid Ethereum address"),
      `expected to contain 'Invalid Ethereum address', actual: ${result.error}`)
  })

  it("validateTransaction: zero address throws 'Cannot send to zero address'", () => {
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${ZERO_ADDR}", amount: "1", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Cannot send to zero address"),
      `expected to contain 'Cannot send to zero address', actual: ${result.error}`)
  })

  it("validateTransaction: self-transfer (sending to own EOA address) throws error", () => {
    // Use the wallet's own address as recipient
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      import { getAddress } from "./scripts/lib/keystore.js";
      try {
        const myAddr = getAddress("eoa");
        await validateTransaction({
          to: myAddr, amount: "1", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Cannot send to own address"),
      `expected to contain 'Cannot send to own address', actual: ${result.error}`)
  })

  // ---- validateTransaction: limit checking ----

  it("validateTransaction: single transaction exceeding limit (USDC max 500) throws error", () => {
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "501", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Per-transaction limit") && result.error.includes("exceeded"),
      `expected to contain limit exceeded info, actual: ${result.error}`)
  })

  it("validateTransaction: amount within limit passes validation", () => {
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "100", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.equal(result.ok, true)
  })

  it("validateTransaction: daily limit check (triggers daily limit after recording transactions)", () => {
    // First write transaction records near the daily limit (USDC daily limit 1000)
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      // Write 900 USDC of history records
      logTransaction({ chain: "bsc", chainId: 56, to: "${VALID_RECIPIENT}", amount: "450", asset: "USDC", type: "transfer" });
      logTransaction({ chain: "bsc", chainId: 56, to: "${VALID_RECIPIENT}", amount: "450", asset: "USDC", type: "transfer" });
      try {
        // Sending 200 more would exceed the 1000 daily limit
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "200", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Daily limit exceeded"),
      `expected to contain 'Daily limit exceeded', actual: ${result.error}`)
  })

  it("validateTransaction: native transfer (no asset) resolves to chain's native currency for limit checking", () => {
    // BSC native currency is BNB, no BNB in perTransactionMax, uses default: 250
    // Sending over 250 should trigger limit
    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "300", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Per-transaction limit") && result.error.includes("exceeded"),
      `expected native transfer over-limit error, actual: ${result.error}`)
  })

  // ---- validateBatchOps ----

  it("validateBatchOps: raw call type throws 'Raw call type not allowed in batch'", () => {
    const result = execValidator(ctx.home, `
      import { validateBatchOps } from "./scripts/lib/tx-validator.js";
      try {
        await validateBatchOps([
          { type: "raw", to: "${VALID_RECIPIENT}", amount: "1", asset: "USDC" }
        ], "bsc");
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("Raw call type not allowed in batch"),
      `expected to contain 'Raw call type not allowed in batch', actual: ${result.error}`)
  })

  it("validateBatchOps: valid transfer operations pass validation", () => {
    const result = execValidator(ctx.home, `
      import { validateBatchOps } from "./scripts/lib/tx-validator.js";
      try {
        await validateBatchOps([
          { type: "transfer", to: "${VALID_RECIPIENT}", amount: "10", asset: "USDC" },
          { type: "transfer", to: "${VALID_RECIPIENT}", amount: "20", asset: "USDT" }
        ], "bsc");
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.equal(result.ok, true)
  })

  // ---- Allowlist mode ----

  it("allowlistMode: when enabled, recipient not in allowlist throws error", () => {
    // Modify config to enable allowlistMode
    const configPath = join(ctx.walletDir, "config.json")
    const config = JSON.parse(readFileSync(configPath, "utf8"))
    config.allowlistMode = true
    config.allowlistedRecipients = ["0xdead000000000000000000000000000000000001"]
    writeFileSync(configPath, JSON.stringify(config))

    const result = execValidator(ctx.home, `
      import { validateTransaction } from "./scripts/lib/tx-validator.js";
      try {
        await validateTransaction({
          to: "${VALID_RECIPIENT}", amount: "1", asset: "USDC", chain: "bsc"
        });
        console.log(JSON.stringify({ ok: true }));
      } catch (e) {
        console.log(JSON.stringify({ error: e.message }));
      }
    `)
    assert.ok(result.error)
    assert.ok(result.error.includes("not in allowlist"),
      `expected to contain 'not in allowlist', actual: ${result.error}`)
  })

  // ---- CLI integration ----

  it("send command: invalid address triggers validation error", () => {
    const res = runCli(
      `send --token ${ctx.token} --to ${INVALID_ADDR} --amount 1 --asset USDC --chain bsc`,
      ctx.env,
    )
    assert.notEqual(res.exitCode, 0)
    assert.ok(
      res.stderr.includes("Invalid Ethereum address") || res.stdout.includes("Invalid Ethereum address"),
      "CLI should output invalid address error",
    )
  })

  it("send command: zero address triggers validation error", () => {
    const res = runCli(
      `send --token ${ctx.token} --to ${ZERO_ADDR} --amount 1 --asset USDC --chain bsc`,
      ctx.env,
    )
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr + res.stdout
    assert.ok(output.includes("Cannot send to zero address"),
      "CLI should output zero address error")
  })
})
