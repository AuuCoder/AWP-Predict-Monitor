/**
 * keystore.js integration tests
 *
 * Tests wallet initialization, import, signer loading, password change, mnemonic export, etc.
 * Each test uses createTestEnv() to get an isolated wallet environment and tests via CLI calls.
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync, writeFileSync, statSync, existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { execFileSync } from "node:child_process"
import {
  createTestEnv,
  runCli,
  readWalletFile,
  listWalletDir,
  CLI_PATH,
  TEST_PASSWORD,
  TEST_PASSWORD_NEW,
} from "../helpers/setup.js"

// Known test mnemonic and its corresponding address (used for import tests)
const TEST_MNEMONIC = "test test test test test test test test test test test junk"
// ethers.Wallet.fromPhrase(TEST_MNEMONIC).address
const TEST_MNEMONIC_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

/**
 * Helper function: import mnemonic via CLI (bypasses runCli's space-splitting issue)
 * runCli splits arguments by whitespace, which cannot correctly pass multi-word mnemonics
 */
function runCliImport(mnemonic, env, opts = {}) {
  try {
    const stdout = execFileSync("node", [CLI_PATH, "import", "--mnemonic", mnemonic], {
      env,
      timeout: opts.timeout || 30_000,
      encoding: "utf8",
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

describe("keystore — initWallet", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("creates keystore.enc and meta.json files", () => {
    const res = runCli("init", ctx.env)
    assert.equal(res.exitCode, 0, `init failed: ${res.stderr}`)
    assert.ok(existsSync(join(ctx.walletDir, "keystore.enc")), "keystore.enc should exist")
    assert.ok(existsSync(join(ctx.walletDir, "meta.json")), "meta.json should exist")
  })

  it("keystore.enc is valid V3 JSON (contains Crypto field)", () => {
    runCli("init", ctx.env)
    const ksRaw = readFileSync(join(ctx.walletDir, "keystore.enc"), "utf8")
    const ks = JSON.parse(ksRaw)
    // ethers v6 uses uppercase 'Crypto' field name (conforming to V3 spec)
    assert.ok(ks.Crypto || ks.crypto, "keystore.enc should contain 'Crypto' or 'crypto' field (V3 format)")
    assert.equal(ks.version, 3, "keystore version should be 3")
  })

  it("meta.json contains address and smartAccounts fields", () => {
    runCli("init", ctx.env)
    const meta = JSON.parse(readWalletFile(ctx.walletDir, "meta.json"))
    assert.ok(meta.address, "meta.json should contain address")
    assert.ok(typeof meta.address === "string", "address should be a string")
    assert.ok(meta.address.startsWith("0x"), "address should start with 0x")
    assert.ok("smartAccounts" in meta, "meta.json should contain smartAccounts field")
  })

  it("duplicate initialization throws 'Wallet already exists' error", () => {
    const first = runCli("init", ctx.env)
    assert.equal(first.exitCode, 0)
    const second = runCli("init", ctx.env)
    assert.notEqual(second.exitCode, 0)
    const output = second.stderr || second.stdout
    assert.ok(output.includes("Wallet already exists"), `should contain 'Wallet already exists', actual: ${output}`)
  })

  it("throws error when WALLET_PASSWORD is not set", () => {
    const envNoPw = { ...ctx.env }
    delete envNoPw.WALLET_PASSWORD
    const res = runCli("init", envNoPw)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(output.includes("WALLET_PASSWORD"), `should show WALLET_PASSWORD-related error, actual: ${output}`)
  })
})

describe("keystore — importWallet", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("correctly imports wallet via mnemonic", () => {
    const res = runCliImport(TEST_MNEMONIC, ctx.env)
    assert.equal(res.exitCode, 0, `import failed: ${res.stderr}`)
    assert.equal(res.json.status, "imported")
  })

  it("imported address matches the known mnemonic's corresponding address", () => {
    const res = runCliImport(TEST_MNEMONIC, ctx.env)
    assert.equal(res.exitCode, 0, `import failed: ${res.stderr}`)
    assert.equal(res.json.address, TEST_MNEMONIC_ADDRESS)
  })
})

describe("keystore — loadSigner (via unlock + status)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("returns a viem account with the correct address", () => {
    // Import with known mnemonic, then unlock + status to verify address
    runCliImport(TEST_MNEMONIC, ctx.env)
    const unlockRes = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlockRes.exitCode, 0, `unlock failed: ${unlockRes.stderr}`)
    const token = unlockRes.json.sessionToken

    const statusRes = runCli(`status --token ${token}`, ctx.env)
    assert.equal(statusRes.exitCode, 0, `status failed: ${statusRes.stderr}`)
    assert.equal(statusRes.json.address, TEST_MNEMONIC_ADDRESS)
  })

  it("throws 'Wrong password' error when password is incorrect", () => {
    runCli("init", ctx.env)
    const wrongPwEnv = { ...ctx.env, WALLET_PASSWORD: "wrong-password-xxx" }
    const res = runCli("unlock --duration 3600", wrongPwEnv)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(output.includes("Wrong password"), `should contain 'Wrong password', actual: ${output}`)
  })
})

describe("keystore — unlockAndCache / readSignerCache", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("creates encrypted .signer-cache file after unlock", () => {
    runCli("init", ctx.env)
    runCli("unlock --duration 3600", ctx.env)
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    assert.ok(existsSync(cacheDir), ".signer-cache directory should exist")
    const files = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
    assert.ok(files.length > 0, "should have at least one .key cache file")
  })

  it("cache file is not plaintext (AES-GCM encrypted, starts with random IV bytes)", () => {
    runCli("init", ctx.env)
    runCli("unlock --duration 3600", ctx.env)
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    const files = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
    const cacheContent = readFileSync(join(cacheDir, files[0]))

    // Cache file is binary format: iv(12) + tag(16) + ciphertext
    // Should not be valid UTF-8 JSON
    let isPlaintext = false
    try {
      JSON.parse(cacheContent.toString("utf8"))
      isPlaintext = true
    } catch {
      // Expected: parse failure means it's not plaintext JSON
    }
    assert.ok(!isPlaintext, "cache file should not be plaintext JSON")

    // Cache file should contain at least iv(12) + tag(16) = 28 bytes
    assert.ok(cacheContent.length >= 28, "cache file should be at least 28 bytes (IV + Auth Tag)")
  })

  it("readSignerCache verified via a second status query that cache is readable", () => {
    // Initialize and unlock, verify cache works via status
    runCliImport(TEST_MNEMONIC, ctx.env)
    const unlockRes = runCli("unlock --duration 3600", ctx.env)
    assert.equal(unlockRes.exitCode, 0)

    // Use status to verify cache works correctly (internally uses signer cache)
    const statusRes = runCli(`status --token ${unlockRes.json.sessionToken}`, ctx.env)
    assert.equal(statusRes.exitCode, 0)
    assert.equal(statusRes.json.address, TEST_MNEMONIC_ADDRESS)
  })
})

describe("keystore — clearSignerCache (via lock command)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("clears all .key cache files after lock", () => {
    runCli("init", ctx.env)
    runCli("unlock --duration 3600", ctx.env)

    // Confirm cache files exist
    const cacheDir = join(ctx.walletDir, ".signer-cache")
    let keyFiles = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
    assert.ok(keyFiles.length > 0, "should have cache files before lock")

    // Execute lock
    const lockRes = runCli("lock", ctx.env)
    assert.equal(lockRes.exitCode, 0)

    // Cache files should be cleared
    if (existsSync(cacheDir)) {
      keyFiles = readdirSync(cacheDir).filter((f) => f.endsWith(".key"))
      assert.equal(keyFiles.length, 0, "should have no .key cache files after lock")
    }
  })
})

describe("keystore — changePassword", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("after password change, old password fails and new password succeeds", () => {
    runCli("init", ctx.env)

    // Change password
    const changePwEnv = { ...ctx.env, NEW_WALLET_PASSWORD: TEST_PASSWORD_NEW }
    const changeRes = runCli("change-password", changePwEnv)
    assert.equal(changeRes.exitCode, 0, `change-password failed: ${changeRes.stderr}`)

    // Old password unlock should fail
    const oldPwRes = runCli("unlock --duration 3600", ctx.env)
    assert.notEqual(oldPwRes.exitCode, 0, "old password should not be able to unlock")

    // New password unlock should succeed
    const newPwEnv = { ...ctx.env, WALLET_PASSWORD: TEST_PASSWORD_NEW }
    const newPwRes = runCli("unlock --duration 3600", newPwEnv)
    assert.equal(newPwRes.exitCode, 0, "new password should be able to unlock")
  })
})

describe("keystore — exportMnemonic", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("returns a 12-word mnemonic phrase", () => {
    runCli("init", ctx.env)
    const res = runCli("export", ctx.env)
    assert.equal(res.exitCode, 0, `export failed: ${res.stderr}`)
    assert.ok(res.json.mnemonic, "should return mnemonic field")
    const words = res.json.mnemonic.trim().split(/\s+/)
    assert.equal(words.length, 12, `mnemonic should be 12 words, actual: ${words.length}`)
  })
})

describe("keystore — getAddress", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("getAddress('eoa') returns the correct address", () => {
    runCliImport(TEST_MNEMONIC, ctx.env)

    // Get EOA address via receive command
    const receiveRes = runCli("receive", ctx.env)
    assert.equal(receiveRes.exitCode, 0, `receive failed: ${receiveRes.stderr}`)
    assert.equal(receiveRes.json.eoaAddress, TEST_MNEMONIC_ADDRESS)
  })

  it("getAddress('smart', 56) returns null for undeployed smart account", () => {
    runCliImport(TEST_MNEMONIC, ctx.env)
    const receiveRes = runCli("receive --chain 56", ctx.env)
    assert.equal(receiveRes.exitCode, 0, `receive failed: ${receiveRes.stderr}`)
    assert.equal(receiveRes.json.smartAccountAddress, null, "undeployed smart account address should be null")
  })
})

describe("keystore — saveSmartAccountAddress", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("saves and correctly reads smart account address", () => {
    // Initialize wallet to create meta.json
    runCliImport(TEST_MNEMONIC, ctx.env)
    const fakeSmartAddr = "0x1234567890abcdef1234567890abcdef12345678"

    // Manually write smartAccounts to meta.json
    const metaPath = join(ctx.walletDir, "meta.json")
    const meta = JSON.parse(readFileSync(metaPath, "utf8"))
    meta.smartAccounts["56"] = fakeSmartAddr
    writeFileSync(metaPath, JSON.stringify(meta), { mode: 0o600 })

    // Verify via receive command
    const receiveRes = runCli("receive --chain 56", ctx.env)
    assert.equal(receiveRes.exitCode, 0, `receive failed: ${receiveRes.stderr}`)
    assert.equal(receiveRes.json.smartAccountAddress, fakeSmartAddr)
  })
})

describe("keystore — file permissions", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("keystore.enc permissions should be 0o600 (owner read/write only)", () => {
    runCli("init", ctx.env)
    const ksPath = join(ctx.walletDir, "keystore.enc")
    const stat = statSync(ksPath)
    // Extract the lower 9 bits of file permissions (rwx rwx rwx)
    const mode = stat.mode & 0o777
    assert.equal(mode, 0o600, `keystore.enc permissions should be 0600, actual: 0${mode.toString(8)}`)
  })
})
