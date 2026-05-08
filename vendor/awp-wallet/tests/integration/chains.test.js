/**
 * chains.js integration tests
 *
 * Tests chain resolution, config loading, RPC URL resolution, token info, etc.
 * Pure functions (resolveChainId built-in mappings) can be imported directly for testing;
 * config-dependent functions are tested via CLI calls to avoid module-level cache issues.
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { writeFileSync, readFileSync, unlinkSync } from "node:fs"
import { join } from "node:path"
import { createTestEnv, runCli } from "../helpers/setup.js"

// resolveChainId resolution of built-in aliases does not depend on config, can be imported directly
import { resolveChainId, viemChain } from "../../scripts/lib/chains.js"

/**
 * Helper function: read the config file from the wallet directory
 */
function readConfigFile(walletDir) {
  return JSON.parse(readFileSync(join(walletDir, "config.json"), "utf8"))
}

describe("chains — resolveChainId (built-in aliases)", () => {
  it("resolves 'bsc' to 56", () => {
    assert.equal(resolveChainId("bsc"), 56)
  })

  it("resolves string '56' to numeric 56", () => {
    assert.equal(resolveChainId("56"), 56)
  })

  it("returns numeric 56 as-is", () => {
    assert.equal(resolveChainId(56), 56)
  })

  it("resolves 'base' to 8453", () => {
    assert.equal(resolveChainId("base"), 8453)
  })

  it("resolves 'ethereum' to 1", () => {
    assert.equal(resolveChainId("ethereum"), 1)
  })

  it("resolves 'avax' to 43114", () => {
    assert.equal(resolveChainId("avax"), 43114)
  })

  it("resolves 'ftm' to 250", () => {
    assert.equal(resolveChainId("ftm"), 250)
  })

  it("throws 'Unknown chain' error for unrecognized chain name", () => {
    assert.throws(
      () => resolveChainId("nonexistent-chain-xyz"),
      (err) => err.message.includes("Unknown chain")
    )
  })
})

describe("chains — viemChain", () => {
  it("returns correct chain object for known chainId (56 -> BNB Smart Chain)", () => {
    const chain = viemChain(56)
    assert.equal(chain.id, 56)
    assert.equal(chain.name, "BNB Smart Chain")
  })

  it("throws error for unknown chainId without rpcUrl", () => {
    assert.throws(
      () => viemChain(999999),
      (err) => err.message.includes("unknown") || err.message.includes("rpc")
    )
  })

  it("creates custom chain (defineChain) for unknown chainId with rpcUrl", () => {
    const chain = viemChain(777777, "https://rpc.example.com")
    assert.equal(chain.id, 777777)
    assert.equal(chain.name, "Chain 777777")
    assert.equal(chain.rpcUrls.default.http[0], "https://rpc.example.com")
  })
})

describe("chains — chainConfig (via CLI)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("'bsc' returns config with chainId 56 and includes tokens", () => {
    // Use chain-info command to indirectly test chainConfig
    const res = runCli("chain-info --chain bsc", ctx.env)
    assert.equal(res.exitCode, 0)
    assert.equal(res.json.chainId, 56)
    assert.ok(res.json.configuredTokens.length > 0, "should contain configured tokens")
    assert.ok(res.json.configuredTokens.includes("USDC"), "should contain USDC")
  })

  it("numeric 56 also returns BSC config", () => {
    const res = runCli("chain-info --chain 56", ctx.env)
    assert.equal(res.exitCode, 0)
    assert.equal(res.json.chainId, 56)
    assert.equal(res.json.name, "BNB Smart Chain")
  })

  it("unknown chain is not in the configured chains list", () => {
    const res = runCli("chains", ctx.env)
    assert.equal(res.exitCode, 0)
    const chainNames = res.json.chains.map((c) => c.name)
    assert.ok(chainNames.includes("bsc"), "should contain bsc")
    assert.ok(!chainNames.includes("nonexistent"), "should not contain nonexistent chain")
  })
})

describe("chains — getRpcUrl (environment variable substitution)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("'bsc' rpcOverrides supports environment variable substitution", () => {
    // In config, bsc's rpcOverrides is "{BSC_RPC_URL}"
    ctx.env.BSC_RPC_URL = "https://test-bsc-rpc.example.com"
    // chain-info internally calls getRpcUrl; successful execution means substitution worked
    const res = runCli("chain-info --chain bsc", ctx.env)
    assert.equal(res.exitCode, 0)
    assert.equal(res.json.chainId, 56)
  })
})

describe("chains — loadConfig error handling (via CLI)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("throws 'Config not found' when config file is missing", () => {
    // Delete the config file
    unlinkSync(join(ctx.walletDir, "config.json"))
    const res = runCli("chains", ctx.env)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(output.includes("Config not found"), `should contain 'Config not found', actual: ${output}`)
  })

  it("throws 'Config file corrupted' when config file has invalid JSON", () => {
    // Write corrupted JSON
    writeFileSync(join(ctx.walletDir, "config.json"), "{invalid json!!!")
    const res = runCli("chains", ctx.env)
    assert.notEqual(res.exitCode, 0)
    const output = res.stderr || res.stdout
    assert.ok(output.includes("Config file corrupted"), `should contain 'Config file corrupted', actual: ${output}`)
  })
})

describe("chains — tokenInfo (token config validation)", () => {
  let ctx

  beforeEach(() => {
    ctx = createTestEnv()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  it("USDC decimals on BSC is 18 (not 6, BSC-specific)", () => {
    const configRaw = readConfigFile(ctx.walletDir)
    const bscUsdc = configRaw.chains.bsc.tokens.USDC
    assert.equal(bscUsdc.decimals, 18, "USDC decimals on BSC should be 18")
  })

  it("USDT decimals on BSC is 18", () => {
    const configRaw = readConfigFile(ctx.walletDir)
    const bscUsdt = configRaw.chains.bsc.tokens.USDT
    assert.equal(bscUsdt.decimals, 18, "USDT decimals on BSC should be 18")
  })

  it("unknown token symbol on BSC does not exist in config", () => {
    const configRaw = readConfigFile(ctx.walletDir)
    const unknownToken = configRaw.chains.bsc.tokens["NONEXISTENT"]
    assert.equal(unknownToken, undefined, "NONEXISTENT token should not exist")
  })
})
