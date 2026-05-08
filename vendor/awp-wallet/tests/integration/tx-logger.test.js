/**
 * tx-logger.js integration tests
 * Tests transaction log writing, reading, hash chaining, and integrity verification
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { execFileSync } from "node:child_process"
import { readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import {
  createTestEnv, runCli, initAndUnlock, readWalletFile, PROJECT_ROOT,
} from "../helpers/setup.js"

/**
 * Execute tx-logger functions in an isolated environment, returns JSON result
 * Uses subprocess to avoid module-level HOME cache issues
 */
function execLogger(home, code) {
  const script = `
    process.env.HOME = ${JSON.stringify(home)};
    ${code}
  `
  const out = execFileSync("node", ["--input-type=module", "-e", script], {
    env: { ...process.env, HOME: home },
    cwd: PROJECT_ROOT,
    encoding: "utf8",
    timeout: 30_000,
  }).trim()
  return out ? JSON.parse(out) : null
}

describe("tx-logger", () => {
  let ctx

  beforeEach(() => {
    ctx = initAndUnlock()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  // ---- logTransaction ----

  it("logTransaction: appends record to tx-log.jsonl", () => {
    execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      logTransaction({ chain: "bsc", to: "0xABCD", amount: "10", asset: "USDC" });
      console.log(JSON.stringify({ ok: true }));
    `)
    const content = readWalletFile(ctx.walletDir, "tx-log.jsonl")
    assert.ok(content, "tx-log.jsonl should exist")
    const lines = content.trim().split("\n").filter(Boolean)
    assert.equal(lines.length, 1, "should have one record")
  })

  it("logTransaction: entry contains timestamp, _prevHash, _hash fields", () => {
    const entry = execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      const e = logTransaction({ chain: "bsc", to: "0xABCD", amount: "5", asset: "USDT" });
      console.log(JSON.stringify(e));
    `)
    assert.ok(entry.timestamp, "should have timestamp")
    assert.ok(entry._prevHash, "should have _prevHash")
    assert.ok(entry._hash, "should have _hash")
  })

  it("logTransaction: first record's _prevHash is '0'", () => {
    const entry = execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      const e = logTransaction({ chain: "bsc", to: "0x1111", amount: "1" });
      console.log(JSON.stringify(e));
    `)
    assert.equal(entry._prevHash, "0")
  })

  it("logTransaction: second record's _prevHash equals first record's _hash (hash chain)", () => {
    const entries = execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      const e1 = logTransaction({ chain: "bsc", to: "0x1111", amount: "1" });
      const e2 = logTransaction({ chain: "bsc", to: "0x2222", amount: "2" });
      console.log(JSON.stringify([e1, e2]));
    `)
    assert.equal(entries[1]._prevHash, entries[0]._hash,
      "second record's _prevHash should equal first record's _hash")
  })

  it("logTransaction: hash value is SHA256(prevHash + JSON(content))", () => {
    const entry = execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      const e = logTransaction({ chain: "bsc", to: "0xAAAA", amount: "42" });
      console.log(JSON.stringify(e));
    `)
    // Recompute hash according to logTransaction's logic
    const { _prevHash, _hash, ...content } = entry
    const hashInput = _prevHash + JSON.stringify(content)
    const expected = createHash("sha256").update(hashInput).digest("hex")
    assert.equal(_hash, expected, "hash value should match manual computation")
  })

  // ---- getHistory ----

  it("getHistory: returns [] when no log file exists", () => {
    const result = execLogger(ctx.home, `
      import { getHistory } from "./scripts/lib/tx-logger.js";
      console.log(JSON.stringify(getHistory()));
    `)
    assert.deepStrictEqual(result, [])
  })

  it("getHistory: returns all entries", () => {
    const result = execLogger(ctx.home, `
      import { logTransaction, getHistory } from "./scripts/lib/tx-logger.js";
      logTransaction({ chain: "bsc", to: "0x1", amount: "1" });
      logTransaction({ chain: "bsc", to: "0x2", amount: "2" });
      logTransaction({ chain: "ethereum", to: "0x3", amount: "3" });
      console.log(JSON.stringify(getHistory()));
    `)
    assert.equal(result.length, 3, "should return all 3 records")
  })

  it("getHistory: filters by chain name", () => {
    const result = execLogger(ctx.home, `
      import { logTransaction, getHistory } from "./scripts/lib/tx-logger.js";
      logTransaction({ chain: "bsc", to: "0x1", amount: "1" });
      logTransaction({ chain: "ethereum", to: "0x2", amount: "2" });
      logTransaction({ chain: "bsc", to: "0x3", amount: "3" });
      console.log(JSON.stringify(getHistory("bsc")));
    `)
    assert.equal(result.length, 2, "should only return records for bsc chain")
    assert.ok(result.every(e => e.chain === "bsc"))
  })

  it("getHistory: respects the limit parameter", () => {
    const result = execLogger(ctx.home, `
      import { logTransaction, getHistory } from "./scripts/lib/tx-logger.js";
      for (let i = 0; i < 10; i++) {
        logTransaction({ chain: "bsc", to: "0x" + i, amount: String(i) });
      }
      console.log(JSON.stringify(getHistory(null, 3)));
    `)
    assert.equal(result.length, 3, "should only return the last 3 records")
  })

  // ---- verifyIntegrity ----

  it("verifyIntegrity: returns { valid: true, entries: 0 } when no log exists", () => {
    const result = execLogger(ctx.home, `
      import { verifyIntegrity } from "./scripts/lib/tx-logger.js";
      console.log(JSON.stringify(verifyIntegrity()));
    `)
    assert.deepStrictEqual(result, { valid: true, entries: 0 })
  })

  it("verifyIntegrity: returns valid: true after legitimate records", () => {
    const result = execLogger(ctx.home, `
      import { logTransaction, verifyIntegrity } from "./scripts/lib/tx-logger.js";
      logTransaction({ chain: "bsc", to: "0xA", amount: "10" });
      logTransaction({ chain: "ethereum", to: "0xB", amount: "20" });
      console.log(JSON.stringify(verifyIntegrity()));
    `)
    assert.equal(result.valid, true)
    assert.equal(result.entries, 2)
  })

  it("verifyIntegrity: detects tampering (returns valid: false after modifying _hash)", () => {
    // First write legitimate records
    execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      logTransaction({ chain: "bsc", to: "0xA", amount: "10" });
      logTransaction({ chain: "bsc", to: "0xB", amount: "20" });
      console.log(JSON.stringify({ ok: true }));
    `)

    // Manually tamper with the _hash of the first record in the log file
    const logPath = join(ctx.walletDir, "tx-log.jsonl")
    const logContent = readFileSync(logPath, "utf8")
    const lines = logContent.trim().split("\n")
    const entry = JSON.parse(lines[0])
    entry._hash = "0000000000000000000000000000000000000000000000000000000000000000"
    lines[0] = JSON.stringify(entry)
    writeFileSync(logPath, lines.join("\n") + "\n")

    // Integrity verification should fail
    const result = execLogger(ctx.home, `
      import { verifyIntegrity } from "./scripts/lib/tx-logger.js";
      console.log(JSON.stringify(verifyIntegrity()));
    `)
    assert.equal(result.valid, false, "should detect integrity issue after tampering")
  })

  // ---- Hash chain consistency ----

  it("hash chain consistency: write 5 records, verify all links are correct", () => {
    const entries = execLogger(ctx.home, `
      import { logTransaction } from "./scripts/lib/tx-logger.js";
      const results = [];
      for (let i = 0; i < 5; i++) {
        results.push(logTransaction({ chain: "bsc", to: "0x" + i, amount: String(i + 1) }));
      }
      console.log(JSON.stringify(results));
    `)

    assert.equal(entries.length, 5)
    // First record's _prevHash should be "0"
    assert.equal(entries[0]._prevHash, "0")

    // Each record's _prevHash should equal the previous record's _hash
    for (let i = 1; i < entries.length; i++) {
      assert.equal(entries[i]._prevHash, entries[i - 1]._hash,
        `record ${i + 1}'s _prevHash should equal record ${i}'s _hash`)
    }

    // Verify hash computation is correct for each record
    for (const entry of entries) {
      const { _prevHash, _hash, ...content } = entry
      const hashInput = _prevHash + JSON.stringify(content)
      const expected = createHash("sha256").update(hashInput).digest("hex")
      assert.equal(_hash, expected, "hash value should be correct")
    }
  })

  // ---- CLI integration ----

  it("verify-log command: returns valid: true when no log exists", () => {
    const res = runCli("verify-log", ctx.env)
    assert.equal(res.exitCode, 0)
    assert.equal(res.json.valid, true)
    assert.equal(res.json.entries, 0)
  })

  it("history command: returns empty array when no records exist", () => {
    const res = runCli(`history --token ${ctx.token}`, ctx.env)
    assert.equal(res.exitCode, 0)
    assert.deepStrictEqual(res.json, [])
  })
})
