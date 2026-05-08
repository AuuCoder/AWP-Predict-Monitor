/**
 * E2E lifecycle tests — complete wallet operation flow
 *
 * Covers: init -> unlock -> verify-log -> lock -> duplicate init -> mnemonic import ->
 *         change password -> export mnemonic -> missing password -> setup.sh idempotency
 */
import { describe, it, afterEach } from "node:test"
import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { execFileSync } from "node:child_process"
import {
  createTestEnv,
  runCli,
  initAndUnlock,
  CLI_PATH,
  TEST_PASSWORD,
  TEST_PASSWORD_NEW,
  PROJECT_ROOT,
} from "../helpers/setup.js"

/**
 * Call CLI directly (array arguments, avoids space-splitting issues)
 * Used for arguments containing spaces (e.g., mnemonics)
 */
function runCliArray(args, env) {
  try {
    const stdout = execFileSync("node", [CLI_PATH, ...args], {
      env, timeout: 30_000, encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    })
    let json = null
    try { json = JSON.parse(stdout.trim()) } catch {}
    return { stdout: stdout.trim(), stderr: "", exitCode: 0, json }
  } catch (e) {
    const stderr = (e.stderr || "").trim()
    const stdout = (e.stdout || "").trim()
    let json = null
    try { json = JSON.parse(stderr) } catch {}
    try { if (!json) json = JSON.parse(stdout) } catch {}
    return { stdout, stderr, exitCode: e.status || 1, json }
  }
}

// ----------------------------------------------------------------
// 1. Full lifecycle: init -> unlock -> verify-log -> lock -> token rejected
// ----------------------------------------------------------------
describe("full wallet lifecycle", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("init -> unlock -> verify-log -> lock -> token invalidated", () => {
    ctx = createTestEnv()

    // init
    const init = runCli("init", ctx.env)
    assert.equal(init.exitCode, 0, `init should succeed: ${init.stderr}`)
    assert.equal(init.json.status, "created")
    assert.ok(init.json.address, "should return address")

    // unlock
    const unlock = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlock.exitCode, 0, `unlock should succeed: ${unlock.stderr}`)
    const token = unlock.json.sessionToken
    assert.ok(token, "should return sessionToken")

    // verify-log (new wallet, no transaction records, should be valid)
    const verify = runCli("verify-log", ctx.env)
    assert.equal(verify.exitCode, 0, `verify-log should succeed: ${verify.stderr}`)
    assert.equal(verify.json.valid, true)
    assert.equal(verify.json.entries, 0)

    // lock
    const lock = runCli("lock", ctx.env)
    assert.equal(lock.exitCode, 0, `lock should succeed: ${lock.stderr}`)
    assert.equal(lock.json.status, "locked")

    // Token should be rejected after lock
    const status = runCli(`status --token ${token}`, ctx.env)
    assert.notEqual(status.exitCode, 0, "status should fail after lock")
    const out = status.stderr || status.stdout
    assert.ok(out.includes("Invalid or expired session token"), `should indicate token invalidation, actual: ${out}`)
  })
})

// ----------------------------------------------------------------
// 2. Import wallet via mnemonic
// ----------------------------------------------------------------
describe("mnemonic import", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("import --mnemonic -> unlock -> address matches expected", () => {
    ctx = createTestEnv()
    const mnemonic = "test test test test test test test test test test test junk"
    // This mnemonic corresponds to this address in ethers (Hardhat default account #0)
    const expectedAddr = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

    const imp = runCliArray(["import", "--mnemonic", mnemonic], ctx.env)
    assert.equal(imp.exitCode, 0, `import should succeed: ${imp.stderr}`)
    assert.equal(imp.json.status, "imported")
    assert.equal(imp.json.address, expectedAddr, "address should match hardcoded mnemonic")

    const unlock = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlock.exitCode, 0, `unlock should succeed: ${unlock.stderr}`)
    assert.ok(unlock.json.sessionToken)
  })
})

// ----------------------------------------------------------------
// 3. Change password flow
// ----------------------------------------------------------------
describe("change password", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("change-password -> old password unlock fails -> new password unlock succeeds", () => {
    ctx = createTestEnv()

    // init + unlock (using default TEST_PASSWORD)
    const init = runCli("init", ctx.env)
    assert.equal(init.exitCode, 0, `init should succeed: ${init.stderr}`)

    // Change password
    const chEnv = { ...ctx.env, NEW_WALLET_PASSWORD: TEST_PASSWORD_NEW }
    const ch = runCli("change-password", chEnv)
    assert.equal(ch.exitCode, 0, `change-password should succeed: ${ch.stderr}`)
    assert.equal(ch.json.status, "password_changed")

    // Old password unlock should fail
    const oldEnv = { ...ctx.env, WALLET_PASSWORD: TEST_PASSWORD }
    const fail = runCli("unlock --duration 3600", oldEnv)
    assert.notEqual(fail.exitCode, 0, "old password unlock should fail")
    const failOut = fail.stderr || fail.stdout
    assert.ok(failOut.includes("Wrong password"), `should contain error message, actual: ${failOut}`)

    // New password unlock should succeed
    const newEnv = { ...ctx.env, WALLET_PASSWORD: TEST_PASSWORD_NEW }
    const ok = runCli("unlock --duration 3600", newEnv)
    assert.equal(ok.exitCode, 0, `new password unlock should succeed: ${ok.stderr}`)
    assert.ok(ok.json.sessionToken)
  })
})

// ----------------------------------------------------------------
// 4. Export mnemonic
// ----------------------------------------------------------------
describe("export mnemonic", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("export returns 12 words", () => {
    ctx = createTestEnv()

    const init = runCli("init", ctx.env)
    assert.equal(init.exitCode, 0, `init should succeed: ${init.stderr}`)

    const exp = runCli("export", ctx.env)
    assert.equal(exp.exitCode, 0, `export should succeed: ${exp.stderr}`)
    assert.ok(exp.json.mnemonic, "should return mnemonic")
    const words = exp.json.mnemonic.trim().split(/\s+/)
    assert.equal(words.length, 12, `mnemonic should be 12 words, actual: ${words.length}`)
    assert.ok(exp.json.warning, "should contain warning message")
  })
})

// ----------------------------------------------------------------
// 5. Duplicate initialization is rejected
// ----------------------------------------------------------------
describe("duplicate initialization", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("second init returns 'Wallet already exists' error", () => {
    ctx = createTestEnv()

    const first = runCli("init", ctx.env)
    assert.equal(first.exitCode, 0, `first init should succeed: ${first.stderr}`)

    const second = runCli("init", ctx.env)
    assert.notEqual(second.exitCode, 0, "second init should fail")
    const out = second.stderr || second.stdout
    assert.ok(out.includes("Wallet already exists"), `should indicate wallet already exists, actual: ${out}`)
  })
})

// ----------------------------------------------------------------
// 6. Missing password environment variable
// ----------------------------------------------------------------
describe("missing password", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("running init without WALLET_PASSWORD -> error", () => {
    ctx = createTestEnv()
    // Remove password environment variable
    const envNoPwd = { ...ctx.env }
    delete envNoPwd.WALLET_PASSWORD

    const res = runCli("init", envNoPwd)
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.includes("WALLET_PASSWORD environment variable required"),
      `should indicate missing password variable, actual: ${out}`
    )
  })
})

// ----------------------------------------------------------------
// 7. setup.sh idempotency — running twice does not overwrite existing config
// ----------------------------------------------------------------
describe("setup.sh idempotency", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("running setup.sh twice does not overwrite existing config.json", () => {
    ctx = createTestEnv()
    const setupScript = join(PROJECT_ROOT, "scripts", "setup.sh")

    // First run of setup.sh
    execFileSync("bash", [setupScript], { env: ctx.env, encoding: "utf8", timeout: 30_000 })

    // Read the config generated by first run (createTestEnv already wrote config.json, setup.sh should not overwrite)
    const configPath = join(ctx.walletDir, "config.json")
    const contentBefore = readFileSync(configPath, "utf8")
    const secretPath = join(ctx.walletDir, ".session-secret")
    const secretBefore = readFileSync(secretPath, "utf8")

    // Second run of setup.sh
    execFileSync("bash", [setupScript], { env: ctx.env, encoding: "utf8", timeout: 30_000 })

    // Config file and secret should not be overwritten
    const contentAfter = readFileSync(configPath, "utf8")
    const secretAfter = readFileSync(secretPath, "utf8")
    assert.equal(contentAfter, contentBefore, "config.json should not be overwritten")
    assert.equal(secretAfter, secretBefore, ".session-secret should not be overwritten")
  })
})
