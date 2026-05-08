/**
 * Test helper utilities — creates an isolated wallet environment for each test
 */
import { mkdirSync, writeFileSync, readFileSync, rmSync, existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { randomBytes } from "node:crypto"
import { tmpdir } from "node:os"
import { execFileSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { dirname } from "node:path"

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = join(__dirname, "..", "..")
const CLI_PATH = join(PROJECT_ROOT, "scripts", "wallet-cli.js")
const DEFAULT_CONFIG = join(PROJECT_ROOT, "assets", "default-config.json")

export const TEST_PASSWORD = "test-pwd-123"
export const TEST_PASSWORD_NEW = "new-pwd-456"

/**
 * Create an isolated wallet test environment
 * Returns { walletDir, home, cleanup, env }
 */
export function createTestEnv(opts = {}) {
  const fakeHome = join(tmpdir(), `openclaw-home-${randomBytes(8).toString("hex")}`)
  mkdirSync(fakeHome)
  const realWalletDir = join(fakeHome, ".openclaw-wallet")
  mkdirSync(realWalletDir, { mode: 0o700 })
  mkdirSync(join(realWalletDir, "sessions"), { mode: 0o700 })

  // Copy default configuration
  const configSrc = readFileSync(DEFAULT_CONFIG, "utf8")
  writeFileSync(join(realWalletDir, "config.json"), configSrc, { mode: 0o600 })

  // Generate HMAC session secret
  const secret = randomBytes(32).toString("hex")
  writeFileSync(join(realWalletDir, ".session-secret"), secret, { mode: 0o600 })

  const testEnv = {
    ...process.env,
    HOME: fakeHome,
    WALLET_PASSWORD: opts.password || TEST_PASSWORD,
  }

  return {
    walletDir: realWalletDir,
    home: fakeHome,
    env: testEnv,
    cleanup() {
      try { rmSync(fakeHome, { recursive: true, force: true }) } catch {}
    }
  }
}

/**
 * Execute a CLI command, returns { stdout, stderr, exitCode, json }
 * Uses execFileSync to avoid shell injection
 */
export function runCli(args, env, opts = {}) {
  const argList = args.split(/\s+/).filter(Boolean)
  try {
    const stdout = execFileSync("node", [CLI_PATH, ...argList], {
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

/**
 * Initialize and unlock the wallet, returns { token, address, env, walletDir, home, cleanup }
 */
export function initAndUnlock(opts = {}) {
  const ctx = createTestEnv(opts)
  const initRes = runCli("init", ctx.env)
  if (initRes.exitCode !== 0) throw new Error(`init failed: ${initRes.stderr}`)
  const unlockRes = runCli(`unlock --duration ${opts.duration || 3600}`, ctx.env)
  if (unlockRes.exitCode !== 0) throw new Error(`unlock failed: ${unlockRes.stderr}`)
  return {
    ...ctx,
    token: unlockRes.json.sessionToken,
    address: initRes.json.address,
  }
}

/**
 * Read a file from the wallet directory
 */
export function readWalletFile(walletDir, filename) {
  const path = join(walletDir, filename)
  if (!existsSync(path)) return null
  return readFileSync(path, "utf8")
}

/**
 * List files in a directory
 */
export function listWalletDir(walletDir, subdir = "") {
  const dir = subdir ? join(walletDir, subdir) : walletDir
  if (!existsSync(dir)) return []
  return readdirSync(dir)
}

export { CLI_PATH, PROJECT_ROOT }
