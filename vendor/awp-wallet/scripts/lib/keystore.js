import { Wallet } from "ethers"
import { privateKeyToAccount } from "viem/accounts"
import { randomBytes } from "node:crypto"
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import { WALLET_DIR, WALLETS_DIR, registerWallet } from "./paths.js"

const __dirname = dirname(fileURLToPath(import.meta.url))
const WALLET_PATH = join(WALLET_DIR, "wallet.json")
const LEGACY_KS_PATH = join(WALLET_DIR, "keystore.enc")
const LEGACY_PW_PATH = join(WALLET_DIR, ".wallet-password")
const META_PATH = join(WALLET_DIR, "meta.json")

// --- Load wallet from plaintext wallet.json (with legacy keystore.enc migration) ---

function loadWallet() {
  // New format: plaintext wallet.json
  if (existsSync(WALLET_PATH)) {
    return JSON.parse(readFileSync(WALLET_PATH, "utf8"))
  }

  // Legacy migration: encrypted keystore.enc → wallet.json
  if (existsSync(LEGACY_KS_PATH)) {
    let password
    if (process.env.WALLET_PASSWORD) {
      password = process.env.WALLET_PASSWORD
    } else if (existsSync(LEGACY_PW_PATH)) {
      password = readFileSync(LEGACY_PW_PATH, "utf8").trim()
    } else {
      throw new Error("Legacy encrypted wallet found but no password available. Set WALLET_PASSWORD to migrate.")
    }
    const json = readFileSync(LEGACY_KS_PATH, "utf8")
    const w = Wallet.fromEncryptedJsonSync(json, password)
    // Migrate to plaintext
    const data = { privateKey: w.privateKey, address: w.address }
    if (w.mnemonic) data.mnemonic = w.mnemonic.phrase
    writeFileSync(WALLET_PATH, JSON.stringify(data), { mode: 0o600 })
    return data
  }

  throw new Error("No wallet found. Run 'init' first.")
}

// Persist new wallet to disk
function persistNewWallet(wallet, status) {
  // Provision wallet directory
  if (!existsSync(WALLETS_DIR)) mkdirSync(WALLETS_DIR, { recursive: true, mode: 0o700 })
  if (!existsSync(WALLET_DIR)) mkdirSync(WALLET_DIR, { mode: 0o700 })
  mkdirSync(join(WALLET_DIR, "sessions"), { recursive: true, mode: 0o700 })

  // Write plaintext wallet.json
  const data = { privateKey: wallet.privateKey, address: wallet.address }
  if (wallet.mnemonic) data.mnemonic = wallet.mnemonic.phrase
  writeFileSync(WALLET_PATH, JSON.stringify(data), { mode: 0o600 })

  // Write meta.json
  writeFileSync(META_PATH, JSON.stringify({ address: wallet.address, smartAccounts: {} }), { mode: 0o600 })

  // Copy default config if not present
  const configPath = join(WALLET_DIR, "chains.json")
  if (!existsSync(configPath)) {
    const defaultConfig = join(__dirname, "..", "..", "assets", "default-chains.json")
    if (existsSync(defaultConfig)) writeFileSync(configPath, readFileSync(defaultConfig), { mode: 0o600 })
  }

  // Generate session secret if not present
  const secretPath = join(WALLET_DIR, ".session-secret")
  if (!existsSync(secretPath)) {
    writeFileSync(secretPath, randomBytes(32).toString("hex"), { mode: 0o600 })
  }

  registerWallet(wallet.address)
  _metaCache = null

  return { status, address: wallet.address }
}

// --- Exports ---

export function loadSigner() {
  const data = loadWallet()
  return { account: privateKeyToAccount(data.privateKey) }
}

export function unlockAndCache(sessionId, expiresISO) {
  // No cache needed — plaintext read is fast
  return loadSigner()
}

export function clearSignerCache() {
  // No-op — no cache with plaintext storage
}

export function initWallet() {
  if (existsSync(WALLET_PATH) || existsSync(LEGACY_KS_PATH)) throw new Error("Wallet already exists.")
  return persistNewWallet(Wallet.createRandom(), "created")
}

export function importWallet(mnemonic) {
  if (existsSync(WALLET_PATH) || existsSync(LEGACY_KS_PATH)) throw new Error("Wallet already exists.")
  return persistNewWallet(Wallet.fromPhrase(mnemonic.trim()), "imported")
}

export function exportMnemonic() {
  const data = loadWallet()
  if (!data.mnemonic) throw new Error("Wallet has no mnemonic (imported from private key).")
  return {
    mnemonic: data.mnemonic,
    warning: "Store this offline. Anyone with these words has full access to your funds."
  }
}

export function exportPrivateKey() {
  const data = loadWallet()
  return {
    privateKey: data.privateKey,
    address: data.address,
    warning: "Store this offline. Anyone with this key has full access to your funds."
  }
}

// --- Meta.json with in-process cache ---
let _metaCache = null

function loadMeta() {
  if (_metaCache) return _metaCache
  try {
    _metaCache = JSON.parse(readFileSync(META_PATH, "utf8"))
    return _metaCache
  } catch (err) {
    if (err.code === "ENOENT") throw new Error("No wallet found. Run 'init' first.")
    if (err instanceof SyntaxError) throw new Error("Wallet metadata corrupted. Re-import with 'import --mnemonic'.")
    throw err
  }
}

export function getAddress(type = "eoa", chainId) {
  const meta = loadMeta()
  if (type === "smart") return meta.smartAccounts?.[String(chainId)] || null
  return meta.address
}

export function saveSmartAccountAddress(chainId, addr) {
  const meta = loadMeta()
  if (meta.smartAccounts?.[String(chainId)] === addr) return
  if (!meta.smartAccounts) meta.smartAccounts = {}
  meta.smartAccounts[String(chainId)] = addr
  writeFileSync(META_PATH, JSON.stringify(meta), { mode: 0o600 })
  _metaCache = meta
}

export function getReceiveInfo(chainId) {
  return {
    eoaAddress: getAddress("eoa"),
    smartAccountAddress: chainId ? getAddress("smart", chainId) : null,
    note: "Send to EOA address for direct transactions. Smart Account address is for gasless operations (if deployed)."
  }
}
