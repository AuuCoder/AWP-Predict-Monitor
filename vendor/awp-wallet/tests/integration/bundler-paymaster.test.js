/**
 * Bundler and Paymaster integration tests
 * Tests scripts/lib/bundler.js and scripts/lib/paymaster.js
 * Structural tests (URL template expansion, strategy sorting) do not require API keys
 */
import { describe, it, before, after } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import { initAndUnlock, runCli, createTestEnv } from "../helpers/setup.js"

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = join(__dirname, "..", "..")
const CONFIG_PATH = join(PROJECT_ROOT, "assets", "default-config.json")

// Load default config for structural assertions
const defaultConfig = JSON.parse(readFileSync(CONFIG_PATH, "utf8"))

const HAS_PIMLICO = !!process.env.PIMLICO_API_KEY
const HAS_ANY_BUNDLER_KEY = !!(
  process.env.PIMLICO_API_KEY ||
  process.env.ALCHEMY_API_KEY ||
  process.env.STACKUP_API_KEY
)

describe("createClients", () => {
  let ctx

  before(() => {
    ctx = createTestEnv()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("throws 'No bundler API key' error when no bundler API key is set", () => {
    // Ensure no bundler keys in environment
    const cleanEnv = { ...ctx.env }
    delete cleanEnv.PIMLICO_API_KEY
    delete cleanEnv.ALCHEMY_API_KEY
    delete cleanEnv.STACKUP_API_KEY

    // Indirectly test via chain-info command (it calls createClients)
    const initRes = runCli("init", cleanEnv)
    assert.equal(initRes.exitCode, 0, `init failed: ${initRes.stderr}`)

    const res = runCli("chain-info --chain bsc", cleanEnv)
    assert.equal(res.exitCode, 0, `chain-info failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    // chain-info returns gasless.available = false when no API key
    assert.equal(res.json.gasless.available, false, "gasless should be unavailable without API key")
    assert.ok(
      res.json.gasless.reason.includes("No bundler API key"),
      `error reason should contain 'No bundler API key', actual: ${res.json.gasless.reason}`
    )
  })

  it("returns bundlerClient and paymasterClient when PIMLICO_API_KEY is set", { skip: !HAS_PIMLICO }, async () => {
    // Dynamic import to test in environments with API key
    const { createClients } = await import(join(PROJECT_ROOT, "scripts", "lib", "bundler.js"))
    const clients = createClients("bsc")
    assert.ok(clients.bundlerClient, "should return bundlerClient")
    assert.ok(clients.paymasterClient, "should return paymasterClient")
  })
})

describe("expandUrl template expansion", () => {
  it("Pimlico template uses {chainId} (numeric)", () => {
    const pimlico = defaultConfig.bundlerProviders.find(p => p.name === "pimlico")
    assert.ok(pimlico, "config should contain pimlico provider")
    assert.ok(
      pimlico.bundlerUrlTemplate.includes("{chainId}"),
      "Pimlico bundler URL template should contain {chainId} placeholder"
    )
    // Verify that {chainId} is replaced with a number after expansion
    const expanded = pimlico.bundlerUrlTemplate
      .replace("{chainId}", "56")
      .replace("{key}", "test-key")
    assert.ok(expanded.includes("/56/"), "expanded URL should contain numeric chain ID")
    assert.ok(!expanded.includes("{"), "expanded URL should have no unreplaced placeholders")
  })

  it("Alchemy template uses {chainName} (alchemyName)", () => {
    const alchemy = defaultConfig.bundlerProviders.find(p => p.name === "alchemy")
    assert.ok(alchemy, "config should contain alchemy provider")
    assert.ok(
      alchemy.bundlerUrlTemplate.includes("{chainName}"),
      "Alchemy bundler URL template should contain {chainName} placeholder"
    )
    // Verify that chains in config have alchemyName
    const base = defaultConfig.chains.base
    assert.ok(base.alchemyName, "base chain should have alchemyName")
    const expanded = alchemy.bundlerUrlTemplate
      .replace("{chainName}", base.alchemyName)
      .replace("{key}", "test-key")
    assert.ok(expanded.includes("base-mainnet"), "expanded URL should contain alchemyName")
  })
})

describe("Alchemy bundler/paymaster URL separation", () => {
  it("Alchemy uses different hostnames for bundler and paymaster", () => {
    const alchemy = defaultConfig.bundlerProviders.find(p => p.name === "alchemy")
    assert.ok(alchemy, "config should contain alchemy provider")
    assert.ok(alchemy.paymasterUrlTemplate, "Alchemy should have paymasterUrlTemplate")
    assert.notEqual(
      alchemy.bundlerUrlTemplate,
      alchemy.paymasterUrlTemplate,
      "bundler and paymaster URL templates should differ"
    )
    // Verify hostnames are different (bundler vs paymaster)
    assert.ok(
      alchemy.bundlerUrlTemplate.includes("-bundler."),
      "bundler URL should contain '-bundler.'"
    )
    assert.ok(
      alchemy.paymasterUrlTemplate.includes("-paymaster."),
      "paymaster URL should contain '-paymaster.'"
    )
  })
})

describe("selectStrategy", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("verifying_paymaster chain ranks it first", async () => {
    // base chain is configured with gasStrategy: "verifying_paymaster"
    // Need to set HOME to load the correct config
    const origHome = process.env.HOME
    try {
      process.env.HOME = ctx.home
      // Need to clear module cache to use test environment
      const paymaster = await import(
        join(PROJECT_ROOT, "scripts", "lib", "paymaster.js") + `?t=${Date.now()}`
      )
      const strategies = await paymaster.selectStrategy("base")
      assert.ok(strategies.includes("verifying_paymaster"), "should include verifying_paymaster")
      assert.equal(strategies[0], "verifying_paymaster", "verifying_paymaster should be ranked first")
    } finally {
      process.env.HOME = origHome
    }
  })

  it("always includes smart_account as fallback strategy", async () => {
    const origHome = process.env.HOME
    try {
      process.env.HOME = ctx.home
      const paymaster = await import(
        join(PROJECT_ROOT, "scripts", "lib", "paymaster.js") + `?t2=${Date.now()}`
      )
      const strategies = await paymaster.selectStrategy("bsc")
      assert.ok(
        strategies.includes("smart_account"),
        "strategy list should include smart_account"
      )
      assert.equal(
        strategies[strategies.length - 1],
        "smart_account",
        "smart_account should be last (fallback strategy)"
      )
    } finally {
      process.env.HOME = origHome
    }
  })
})

describe("paymasterFor", () => {
  it("verifying_paymaster strategy returns paymasterClient directly", async () => {
    const ctx = createTestEnv()
    const origHome = process.env.HOME
    try {
      process.env.HOME = ctx.home
      // Initialize wallet to ensure config is available
      runCli("init", ctx.env)

      const paymaster = await import(
        join(PROJECT_ROOT, "scripts", "lib", "paymaster.js") + `?t3=${Date.now()}`
      )
      const mockClient = { getPaymasterData: () => {}, getPaymasterStubData: () => {} }
      const result = paymaster.paymasterFor("bsc", "verifying_paymaster", mockClient)
      assert.equal(result, mockClient, "verifying_paymaster should return the original paymasterClient directly")
    } finally {
      process.env.HOME = origHome
      ctx.cleanup()
    }
  })

  it("erc20_paymaster strategy wraps token context", async () => {
    const ctx = createTestEnv()
    const origHome = process.env.HOME
    try {
      process.env.HOME = ctx.home
      runCli("init", ctx.env)

      const paymaster = await import(
        join(PROJECT_ROOT, "scripts", "lib", "paymaster.js") + `?t4=${Date.now()}`
      )
      // Use mock paymasterClient to verify context injection
      let capturedParams = null
      const mockClient = {
        async getPaymasterData(params) { capturedParams = params; return {} },
        async getPaymasterStubData(params) { capturedParams = params; return {} },
      }
      const wrapped = paymaster.paymasterFor("bsc", "erc20_paymaster", mockClient)
      // Wrapped object should not be the original client
      assert.notEqual(wrapped, mockClient, "erc20_paymaster should return a wrapped object")
      assert.ok(wrapped.getPaymasterData, "wrapped object should have getPaymasterData method")
      assert.ok(wrapped.getPaymasterStubData, "wrapped object should have getPaymasterStubData method")

      // Call wrapped method to verify context injection
      await wrapped.getPaymasterData({ userOperation: {} })
      assert.ok(capturedParams, "should call underlying client")
      assert.ok(capturedParams.context, "should inject context")
      assert.ok(capturedParams.context.token, "context should contain token address")
    } finally {
      process.env.HOME = origHome
      ctx.cleanup()
    }
  })
})

describe("isGaslessAvailable", () => {
  it("returns { available: false } when no API key is set", async () => {
    const ctx = createTestEnv()
    const origHome = process.env.HOME
    // Clear bundler-related environment variables
    const origPimlico = process.env.PIMLICO_API_KEY
    const origAlchemy = process.env.ALCHEMY_API_KEY
    const origStackup = process.env.STACKUP_API_KEY
    try {
      process.env.HOME = ctx.home
      delete process.env.PIMLICO_API_KEY
      delete process.env.ALCHEMY_API_KEY
      delete process.env.STACKUP_API_KEY
      runCli("init", ctx.env)

      const paymaster = await import(
        join(PROJECT_ROOT, "scripts", "lib", "paymaster.js") + `?t5=${Date.now()}`
      )
      const result = await paymaster.isGaslessAvailable("bsc")
      assert.equal(result.available, false, "gasless should be unavailable without API key")
      assert.ok(result.reason, "should include reason description")
    } finally {
      process.env.HOME = origHome
      // Restore environment variables
      if (origPimlico) process.env.PIMLICO_API_KEY = origPimlico
      if (origAlchemy) process.env.ALCHEMY_API_KEY = origAlchemy
      if (origStackup) process.env.STACKUP_API_KEY = origStackup
      ctx.cleanup()
    }
  })
})

describe("chain-info CLI", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("displays gasless availability info", () => {
    const cleanEnv = { ...ctx.env }
    delete cleanEnv.PIMLICO_API_KEY
    delete cleanEnv.ALCHEMY_API_KEY
    delete cleanEnv.STACKUP_API_KEY

    const res = runCli("chain-info --chain bsc", cleanEnv)
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    assert.ok("gasless" in res.json, "should contain gasless field")
    assert.ok("available" in res.json.gasless, "gasless should contain available field")
    assert.equal(typeof res.json.gasless.available, "boolean", "available should be a boolean")
  })
})
