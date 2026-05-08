/**
 * E2E security & error handling tests
 *
 * Covers: wrong password -> session expiry -> scope restrictions -> HMAC tampering ->
 *         signer cache encryption -> file permissions -> lock cleanup ->
 *         no wallet error -> invalid address -> zero address -> per-transaction limit
 */
import { describe, it, afterEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync, writeFileSync, statSync, existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import {
  createTestEnv,
  runCli,
  initAndUnlock,
  readWalletFile,
  listWalletDir,
  TEST_PASSWORD,
} from "../helpers/setup.js"

// ----------------------------------------------------------------
// 1. Wrong password
// ----------------------------------------------------------------
describe("wrong password", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock with wrong password after init -> 'Wrong password — decryption failed.'", () => {
    ctx = createTestEnv()
    const init = runCli("init", ctx.env)
    assert.equal(init.exitCode, 0, `init should succeed: ${init.stderr}`)

    // Use wrong password
    const badEnv = { ...ctx.env, WALLET_PASSWORD: "totally-wrong-password" }
    const unlock = runCli("unlock --duration 3600", badEnv)
    assert.notEqual(unlock.exitCode, 0, "unlock should fail")
    const out = unlock.stderr || unlock.stdout
    assert.ok(
      out.includes("Wrong password") && out.includes("decryption failed"),
      `should indicate wrong password, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 2. Session expiry
// ----------------------------------------------------------------
describe("session expiry", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock --duration 1 -> wait 2 seconds -> balance returns token expired", async () => {
    ctx = initAndUnlock({ duration: 1 })

    // Wait for session to expire
    await sleep(2000)

    const bal = runCli(`balance --token ${ctx.token} --chain bsc`, {
      ...ctx.env,
      BSC_RPC_URL: "https://rpc.example.com",
    })
    assert.notEqual(bal.exitCode, 0, "should fail after expiry")
    const out = bal.stderr || bal.stdout
    assert.ok(
      out.includes("Invalid or expired session token"),
      `should indicate token expiry, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 3. Scope restrictions
// ----------------------------------------------------------------
describe("scope restrictions", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock --scope read -> sign-message requires transfer permission -> rejected", () => {
    ctx = createTestEnv()
    runCli("init", ctx.env)
    const unlock = runCli("unlock --duration 3600 --scope read", ctx.env)
    assert.equal(unlock.exitCode, 0, `unlock should succeed: ${unlock.stderr}`)
    const token = unlock.json.sessionToken

    const sign = runCli(`sign-message --token ${token} --message hello`, ctx.env)
    assert.notEqual(sign.exitCode, 0, "read scope should not allow signing")
    const out = sign.stderr || sign.stdout
    assert.ok(
      out.includes("Scope") && out.includes("insufficient"),
      `should indicate insufficient permissions, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 4. HMAC tampering detection
// ----------------------------------------------------------------
describe("HMAC tampering detection", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("manually tamper session file _hmac -> validateSession fails", () => {
    ctx = initAndUnlock()
    const sessionFile = join(ctx.walletDir, "sessions", ctx.token + ".json")
    assert.ok(existsSync(sessionFile), "session file should exist")

    // Tamper with HMAC
    const data = JSON.parse(readFileSync(sessionFile, "utf8"))
    data._hmac = "0".repeat(64)
    writeFileSync(sessionFile, JSON.stringify(data))

    // Use tampered token to call status
    const status = runCli(`status --token ${ctx.token}`, ctx.env)
    assert.notEqual(status.exitCode, 0, "should fail after tampering")
    const out = status.stderr || status.stdout
    assert.ok(
      out.includes("integrity check failed"),
      `should indicate integrity check failure, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 5. Signer cache is encrypted (not plaintext JSON)
// ----------------------------------------------------------------
describe("signer cache encryption", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("after unlock, .signer-cache/*.key is not valid JSON (encrypted)", () => {
    ctx = initAndUnlock()
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    const files = listWalletDir(ctx.walletDir, ".signer-cache")
    assert.ok(files.length > 0, "signer cache files should exist")

    for (const f of files) {
      if (!f.endsWith(".key")) continue
      const content = readFileSync(join(cacheDir, f))
      let isJson = true
      try {
        JSON.parse(content.toString("utf8"))
      } catch {
        isJson = false
      }
      assert.equal(isJson, false, `${f} should not be plaintext JSON (should be encrypted binary)`)
    }
  })
})

// ----------------------------------------------------------------
// 6. File permission verification
// ----------------------------------------------------------------
describe("file permissions", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("keystore.enc permissions should be 0o600", () => {
    ctx = createTestEnv()
    runCli("init", ctx.env)
    const ksPath = join(ctx.walletDir, "keystore.enc")
    assert.ok(existsSync(ksPath), "keystore.enc should exist")
    const stat = statSync(ksPath)
    // Lower 9 bits of mode: rwx rwx rwx
    const perm = stat.mode & 0o777
    assert.equal(perm, 0o600, `keystore.enc permissions should be 0600, actual: ${perm.toString(8)}`)
  })
})

// ----------------------------------------------------------------
// 7. Lock clears all sessions
// ----------------------------------------------------------------
describe("lock clears all sessions", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock twice -> lock -> both tokens invalidated", () => {
    ctx = createTestEnv()
    runCli("init", ctx.env)

    const u1 = runCli("unlock --duration 3600", ctx.env)
    assert.equal(u1.exitCode, 0)
    const t1 = u1.json.sessionToken

    const u2 = runCli("unlock --duration 3600", ctx.env)
    assert.equal(u2.exitCode, 0)
    const t2 = u2.json.sessionToken

    // lock
    runCli("lock", ctx.env)

    // Both tokens should be invalidated
    for (const token of [t1, t2]) {
      const res = runCli(`status --token ${token}`, ctx.env)
      assert.notEqual(res.exitCode, 0, `token ${token} should be revoked`)
      const out = res.stderr || res.stdout
      assert.ok(out.includes("Invalid or expired session token"), `should indicate token invalidation: ${out}`)
    }
  })
})

// ----------------------------------------------------------------
// 8. Lock clears signer cache
// ----------------------------------------------------------------
describe("lock clears signer cache", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock -> cache has files -> lock -> cache is empty", () => {
    ctx = initAndUnlock()

    // After unlock, cache should have files
    const before = listWalletDir(ctx.walletDir, ".signer-cache").filter(f => f.endsWith(".key"))
    assert.ok(before.length > 0, "should have cache files after unlock")

    // lock
    runCli("lock", ctx.env)

    // After lock, cache should be empty
    const after = listWalletDir(ctx.walletDir, ".signer-cache").filter(f => f.endsWith(".key"))
    assert.equal(after.length, 0, "cache should be empty after lock")
  })
})

// ----------------------------------------------------------------
// 9. Unlock without wallet throws error
// ----------------------------------------------------------------
describe("no wallet error", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("unlock without init -> 'No wallet found'", () => {
    ctx = createTestEnv()
    const res = runCli("unlock --duration 3600", ctx.env)
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.includes("No wallet found") || out.includes("ENOENT") || out.includes("no such file"),
      `should indicate no wallet found, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 10. Invalid address
// ----------------------------------------------------------------
describe("invalid address send", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("send --to INVALID -> address validation error", () => {
    ctx = initAndUnlock()
    const res = runCli(
      `send --token ${ctx.token} --to INVALID --amount 0.01 --chain bsc --mode direct`,
      { ...ctx.env, BSC_RPC_URL: "https://rpc.example.com" }
    )
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.includes("Invalid") && out.includes("address"),
      `should indicate invalid address, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 11. Zero address rejected
// ----------------------------------------------------------------
describe("zero address rejection", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("send --to 0x000...000 -> 'Cannot send to zero address'", () => {
    ctx = initAndUnlock()
    const zero = "0x0000000000000000000000000000000000000000"
    const res = runCli(
      `send --token ${ctx.token} --to ${zero} --amount 0.01 --chain bsc --mode direct`,
      { ...ctx.env, BSC_RPC_URL: "https://rpc.example.com" }
    )
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.includes("zero address"),
      `should indicate zero address, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 12. Per-transaction limit exceeded
// ----------------------------------------------------------------
describe("per-transaction limit", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("send amount exceeding perTransactionMax -> error", () => {
    ctx = initAndUnlock()
    // Default perTransactionMax.default = "250", sending 9999
    const to = "0xdead000000000000000000000000000000000001"
    const res = runCli(
      `send --token ${ctx.token} --to ${to} --amount 9999 --chain bsc --mode direct`,
      { ...ctx.env, BSC_RPC_URL: "https://rpc.example.com" }
    )
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.includes("Per-transaction limit") || out.includes("exceeded"),
      `should indicate limit exceeded, actual: ${out}`
    )
  })
})
