/**
 * session.js integration tests
 *
 * Tests wallet unlock/lock, session token validation, scope checking, etc.
 * All tests are executed via CLI (runCli) because session.js depends on keystore.js state.
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import {
  createTestEnv,
  runCli,
  TEST_PASSWORD,
} from "../helpers/setup.js"

describe("session — unlockWallet", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
    // Initialize wallet before each test
    const initRes = runCli("init", ctx.env)
    assert.equal(initRes.exitCode, 0, `init failed: ${initRes.stderr}`)
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("returns a sessionToken starting with 'wlt_'", () => {
    const res = runCli("unlock --duration 3600", ctx.env)
    assert.equal(res.exitCode, 0, `unlock failed: ${res.stderr}`)
    assert.ok(res.json.sessionToken, "should return sessionToken")
    assert.ok(
      res.json.sessionToken.startsWith("wlt_"),
      `sessionToken should start with 'wlt_', actual: ${res.json.sessionToken}`
    )
  })

  it("creates a session file in sessions/ directory", () => {
    const res = runCli("unlock --duration 3600", ctx.env)
    assert.equal(res.exitCode, 0)
    const sessionsDir = join(ctx.walletDir, "sessions")
    const files = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"))
    assert.ok(files.length > 0, "sessions/ directory should contain session files")
    // Filename should contain the token id
    const tokenFile = res.json.sessionToken + ".json"
    assert.ok(files.includes(tokenFile), `session file ${tokenFile} should exist`)
  })

  it("session file contains HMAC (_hmac field)", () => {
    const res = runCli("unlock --duration 3600", ctx.env)
    assert.equal(res.exitCode, 0)
    const sessionFile = join(ctx.walletDir, "sessions", res.json.sessionToken + ".json")
    const sessionData = JSON.parse(readFileSync(sessionFile, "utf8"))
    assert.ok(sessionData._hmac, "session file should contain _hmac field")
    assert.ok(typeof sessionData._hmac === "string", "_hmac should be a string")
    assert.ok(sessionData._hmac.length > 0, "_hmac should not be empty")
  })

  it("creates .signer-cache file (via unlockAndCache)", () => {
    const res = runCli("unlock --duration 3600", ctx.env)
    assert.equal(res.exitCode, 0)
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    assert.ok(existsSync(cacheDir), ".signer-cache directory should exist")
    const keyFiles = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
    assert.ok(keyFiles.length > 0, "should have .key cache files")
  })
})

describe("session — validateSession (via status command)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
    runCli("init", ctx.env)
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("valid token returns session data with id, scope, created, expires", () => {
    const unlockRes = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // status command internally calls validateSession
    const statusRes = runCli(`status --token ${token}`, ctx.env)
    assert.equal(statusRes.exitCode, 0)
    assert.ok(statusRes.json.sessionValid, "session should be valid")
    assert.ok(statusRes.json.sessionExpires, "should return sessionExpires")

    // Also directly check session file contents
    const sessionFile = join(ctx.walletDir, "sessions", token + ".json")
    const sessionData = JSON.parse(readFileSync(sessionFile, "utf8"))
    assert.ok(sessionData.id, "session data should contain id")
    assert.ok(sessionData.scope, "session data should contain scope")
    assert.ok(sessionData.created, "session data should contain created")
    assert.ok(sessionData.expires, "session data should contain expires")
  })

  it("nonexistent token throws 'Invalid or expired session token'", () => {
    const fakeToken = "wlt_0000000000000000000000000000000000000000"
    const res = runCli(`status --token ${fakeToken}`, ctx.env)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(
      output.includes("Invalid or expired session token"),
      `should contain 'Invalid or expired session token', actual: ${output}`
    )
  })

  it("expired token throws 'Invalid or expired session token'", async () => {
    // Unlock with very short duration (1 second)
    const unlockRes = runCli("unlock --duration 1", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // Wait for token to expire
    await sleep(2000)

    const res = runCli(`status --token ${token}`, ctx.env)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(
      output.includes("Invalid or expired session token"),
      `should contain 'Invalid or expired session token', actual: ${output}`
    )
  })

  it("tampered HMAC throws 'Session token integrity check failed'", () => {
    const unlockRes = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // Tamper with _hmac in session file
    const sessionFile = join(ctx.walletDir, "sessions", token + ".json")
    const sessionData = JSON.parse(readFileSync(sessionFile, "utf8"))
    sessionData._hmac = "deadbeef".repeat(8)  // Forged HMAC
    writeFileSync(sessionFile, JSON.stringify(sessionData), { mode: 0o600 })

    const res = runCli(`status --token ${token}`, ctx.env)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(
      output.includes("Session token integrity check failed"),
      `should contain 'Session token integrity check failed', actual: ${output}`
    )
  })
})

describe("session — requireScope (via history and sign-message commands)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
    runCli("init", ctx.env)
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("'read' scope is sufficient for 'read' operations", () => {
    // Unlock with read scope
    const unlockRes = runCli("unlock --duration 3600 --scope read", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // history command requires read scope
    const historyRes = runCli(`history --token ${token}`, ctx.env)
    // history may fail for other reasons (e.g., chain not configured), but not due to scope
    if (historyRes.exitCode !== 0) {
      const output = historyRes.stderr || historyRes.stdout
      assert.ok(!output.includes("insufficient"), "should not fail due to insufficient scope")
    }
  })

  it("'read' scope is insufficient for 'transfer' operations and throws error", () => {
    // Unlock with read scope
    const unlockRes = runCli("unlock --duration 3600 --scope read", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // sign-message requires transfer scope
    const signRes = runCli(`sign-message --token ${token} --message hello`, ctx.env)
    assert.notEqual(signRes.exitCode, 0)
    const output = signRes.stderr || signRes.stdout
    assert.ok(
      output.includes("insufficient"),
      `should contain 'insufficient', actual: ${output}`
    )
  })

  it("'full' scope is sufficient for any operation", () => {
    // Unlock with full scope (default)
    const unlockRes = runCli("unlock --duration 3600 --scope full", ctx.env)
    assert.equal(unlockRes.exitCode, 0)
    const token = unlockRes.json.sessionToken

    // sign-message requires transfer scope, full should satisfy
    const signRes = runCli(`sign-message --token ${token} --message hello`, ctx.env)
    // sign-message may fail for other reasons, but not due to scope
    if (signRes.exitCode !== 0) {
      const output = signRes.stderr || signRes.stdout
      assert.ok(!output.includes("insufficient"), "full scope should not fail due to insufficient permissions")
      assert.ok(!output.includes("Scope"), "full scope should not trigger Scope error")
    }
  })
})

describe("session — lockWallet", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
    runCli("init", ctx.env)
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("deletes all session files", () => {
    // Create multiple sessions
    runCli("unlock --duration 3600", ctx.env)
    runCli("unlock --duration 3600", ctx.env)
    const sessionsDir = join(ctx.walletDir, "sessions")
    let sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"))
    assert.ok(sessionFiles.length >= 2, "should have at least 2 session files before lock")

    // Execute lock
    const lockRes = runCli("lock", ctx.env)
    assert.equal(lockRes.exitCode, 0)

    // Session files should all be deleted
    sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"))
    assert.equal(sessionFiles.length, 0, "should have no session files after lock")
  })

  it("clears signer cache", () => {
    runCli("unlock --duration 3600", ctx.env)
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    assert.ok(existsSync(cacheDir), ".signer-cache should exist after unlock")

    runCli("lock", ctx.env)

    // Cache directory may exist but should have no .key files
    if (existsSync(cacheDir)) {
      const keyFiles = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
      assert.equal(keyFiles.length, 0, "should have no .key cache files after lock")
    }
  })

  it("returns { status: 'locked' }", () => {
    runCli("unlock --duration 3600", ctx.env)
    const lockRes = runCli("lock", ctx.env)
    assert.equal(lockRes.exitCode, 0)
    assert.deepEqual(lockRes.json, { status: "locked" })
  })
})
