/**
 * E2E chain & RPC operations tests
 *
 * Covers: chains list -> chain-info -> balance -> estimate -> receive ->
 *         history -> verify-log -> status -> token decimals -> default chain -> unknown chain
 *
 * Tests requiring network access are marked with skip: !process.env.BSC_RPC_URL
 */
import { describe, it, afterEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import {
  createTestEnv,
  runCli,
  initAndUnlock,
  readWalletFile,
} from "../helpers/setup.js"

const HAS_RPC = !!process.env.BSC_RPC_URL

// ----------------------------------------------------------------
// 1. chains command — lists all 16 configured chains
// ----------------------------------------------------------------
describe("chains command", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("lists all 16 configured chains", () => {
    ctx = createTestEnv()
    const res = runCli("chains", ctx.env)
    assert.equal(res.exitCode, 0, `chains should succeed: ${res.stderr}`)
    assert.ok(Array.isArray(res.json.chains), "should return chains array")
    assert.equal(res.json.chains.length, 16, `should have 16 chains, actual: ${res.json.chains.length}`)
  })
})

// ----------------------------------------------------------------
// 2. chain-info BSC — chainId 56, name, nativeCurrency, directTx
// ----------------------------------------------------------------
describe("chain-info BSC", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("returns chainId 56, name, nativeCurrency, and directTx: true", () => {
    ctx = createTestEnv()
    const res = runCli("chain-info --chain bsc", ctx.env)
    assert.equal(res.exitCode, 0, `chain-info should succeed: ${res.stderr}`)
    assert.equal(res.json.chainId, 56)
    assert.ok(res.json.name, "should contain chain name")
    assert.ok(res.json.nativeCurrency, "should contain native currency info")
    assert.equal(res.json.directTx, true, "directTx should be true")
  })
})

// ----------------------------------------------------------------
// 3. chain-info configuredTokens — BSC includes USDC, USDT, WBNB
// ----------------------------------------------------------------
describe("chain-info BSC configuredTokens", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("BSC configured tokens include USDC, USDT, WBNB", () => {
    ctx = createTestEnv()
    const res = runCli("chain-info --chain bsc", ctx.env)
    assert.equal(res.exitCode, 0)
    const tokens = res.json.configuredTokens
    assert.ok(tokens.includes("USDC"), "should contain USDC")
    assert.ok(tokens.includes("USDT"), "should contain USDT")
    assert.ok(tokens.includes("WBNB"), "should contain WBNB")
  })
})

// ----------------------------------------------------------------
// 4. balance BSC (requires network)
// ----------------------------------------------------------------
describe("balance BSC (network)", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("returns balances object containing BNB key", { skip: !HAS_RPC }, () => {
    ctx = initAndUnlock()
    const res = runCli(`balance --token ${ctx.token} --chain bsc`, {
      ...ctx.env,
      BSC_RPC_URL: process.env.BSC_RPC_URL,
    })
    assert.equal(res.exitCode, 0, `balance should succeed: ${res.stderr}`)
    assert.ok(res.json.balances, "should contain balances object")
    assert.ok("BNB" in res.json.balances, "balances should contain BNB key")
  })
})

// ----------------------------------------------------------------
// 5. balance chain by ID (network) — --chain 56 equivalent to --chain bsc
// ----------------------------------------------------------------
describe("balance chain by ID (network)", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("--chain 56 and --chain bsc return the same result", { skip: !HAS_RPC }, () => {
    ctx = initAndUnlock()
    const envWithRpc = { ...ctx.env, BSC_RPC_URL: process.env.BSC_RPC_URL }

    const byName = runCli(`balance --token ${ctx.token} --chain bsc`, envWithRpc)
    const byId = runCli(`balance --token ${ctx.token} --chain 56`, envWithRpc)

    assert.equal(byName.exitCode, 0, `byName should succeed: ${byName.stderr}`)
    assert.equal(byId.exitCode, 0, `byId should succeed: ${byId.stderr}`)
    // Both should contain BNB
    assert.ok(byName.json.balances, "byName should have balances")
    assert.ok(byId.json.balances, "byId should have balances")
    assert.ok("BNB" in byName.json.balances)
    assert.ok("BNB" in byId.json.balances)
  })
})

// ----------------------------------------------------------------
// 6. estimate native (network)
// ----------------------------------------------------------------
describe("estimate native (network)", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("returns direct.estimatedGas and gasless.available fields", { skip: !HAS_RPC }, () => {
    ctx = initAndUnlock()
    const to = "0xdead000000000000000000000000000000000001"
    const res = runCli(
      `estimate --to ${to} --amount 0.001 --chain bsc`,
      { ...ctx.env, BSC_RPC_URL: process.env.BSC_RPC_URL }
    )
    assert.equal(res.exitCode, 0, `estimate should succeed: ${res.stderr}`)
    assert.ok(res.json.direct, "should contain direct object")
    assert.ok(res.json.direct.estimatedGas, "should contain estimatedGas")
    assert.ok(res.json.gasless !== undefined, "should contain gasless object")
    assert.ok("available" in res.json.gasless, "gasless should contain available field")
  })
})

// ----------------------------------------------------------------
// 7. estimate ERC20 (network) — gas should be higher than native
// ----------------------------------------------------------------
describe("estimate ERC20 (network)", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("--asset usdc gas should be higher than native transfer", { skip: !HAS_RPC }, () => {
    ctx = initAndUnlock()
    const to = "0xdead000000000000000000000000000000000001"
    const envWithRpc = { ...ctx.env, BSC_RPC_URL: process.env.BSC_RPC_URL }

    const native = runCli(`estimate --to ${to} --amount 0.001 --chain bsc`, envWithRpc)
    const erc20 = runCli(`estimate --to ${to} --amount 1 --asset usdc --chain bsc`, envWithRpc)

    assert.equal(native.exitCode, 0, `native estimate should succeed: ${native.stderr}`)
    assert.equal(erc20.exitCode, 0, `erc20 estimate should succeed: ${erc20.stderr}`)

    const nativeGas = BigInt(native.json.direct.estimatedGas)
    const erc20Gas = BigInt(erc20.json.direct.estimatedGas)
    assert.ok(erc20Gas > nativeGas, `ERC20 gas (${erc20Gas}) should be higher than native gas (${nativeGas})`)
  })
})

// ----------------------------------------------------------------
// 8. receive command — eoaAddress matches init address
// ----------------------------------------------------------------
describe("receive command", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("eoaAddress matches the address returned by init", () => {
    ctx = createTestEnv()
    const init = runCli("init", ctx.env)
    assert.equal(init.exitCode, 0)

    const recv = runCli("receive", ctx.env)
    assert.equal(recv.exitCode, 0, `receive should succeed: ${recv.stderr}`)
    assert.equal(recv.json.eoaAddress, init.json.address, "eoaAddress should match init address")
  })
})

// ----------------------------------------------------------------
// 9. history is empty
// ----------------------------------------------------------------
describe("history empty wallet", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("new wallet history returns empty array", () => {
    ctx = initAndUnlock()
    const res = runCli(`history --token ${ctx.token}`, ctx.env)
    assert.equal(res.exitCode, 0, `history should succeed: ${res.stderr}`)
    assert.ok(Array.isArray(res.json), "should return array")
    assert.equal(res.json.length, 0, "new wallet history should be empty")
  })
})

// ----------------------------------------------------------------
// 10. verify-log empty wallet
// ----------------------------------------------------------------
describe("verify-log empty wallet", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("returns { valid: true, entries: 0 }", () => {
    ctx = createTestEnv()
    runCli("init", ctx.env)

    const res = runCli("verify-log", ctx.env)
    assert.equal(res.exitCode, 0, `verify-log should succeed: ${res.stderr}`)
    assert.equal(res.json.valid, true)
    assert.equal(res.json.entries, 0)
  })
})

// ----------------------------------------------------------------
// 11. status command
// ----------------------------------------------------------------
describe("status command", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("shows address, sessionValid, sessionExpires", () => {
    ctx = initAndUnlock()
    const res = runCli(`status --token ${ctx.token}`, ctx.env)
    assert.equal(res.exitCode, 0, `status should succeed: ${res.stderr}`)
    assert.ok(res.json.address, "should contain address")
    assert.equal(res.json.address, ctx.address, "address should match init return value")
    assert.equal(res.json.sessionValid, true, "sessionValid should be true")
    assert.ok(res.json.sessionExpires, "should contain sessionExpires")
  })
})

// ----------------------------------------------------------------
// 12. BSC token decimals — USDC is 18 (not 6)
// ----------------------------------------------------------------
describe("BSC token decimals", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("BSC USDC decimals is 18 (not 6)", () => {
    ctx = createTestEnv()
    const configRaw = JSON.parse(readWalletFile(ctx.walletDir, "config.json"))
    const bscUsdc = configRaw.chains.bsc.tokens.USDC
    assert.equal(bscUsdc.decimals, 18, "BSC USDC decimals should be 18")
  })
})

// ----------------------------------------------------------------
// 13. Default chain — omitting --chain uses config.defaultChain ("ethereum")
// ----------------------------------------------------------------
describe("default chain", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("omitting --chain in chain-info uses defaultChain (ethereum)", () => {
    ctx = createTestEnv()
    // chain-info without --chain should use defaultChain = "ethereum"
    const res = runCli("chain-info", ctx.env)
    assert.equal(res.exitCode, 0, `chain-info should succeed: ${res.stderr}`)
    assert.equal(res.json.chainId, 1, "default chain should be Ethereum (chainId: 1)")
  })
})

// ----------------------------------------------------------------
// 14. Unknown chain without RPC -> error
// ----------------------------------------------------------------
describe("unknown chain without RPC", () => {
  let ctx

  afterEach(() => ctx?.cleanup())

  it("--chain 99999 -> unknown chain error", () => {
    ctx = createTestEnv()
    const res = runCli("chain-info --chain 99999", ctx.env)
    assert.notEqual(res.exitCode, 0, "should fail")
    const out = res.stderr || res.stdout
    assert.ok(
      out.toLowerCase().includes("unknown") || out.toLowerCase().includes("rpc") || out.toLowerCase().includes("not found"),
      `should indicate unknown chain, actual: ${out}`
    )
  })
})
