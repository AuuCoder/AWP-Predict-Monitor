/**
 * Balance query integration tests — tests scripts/lib/balance.js via CLI
 * Requires network access (BSC RPC); offline environments auto-skip when BSC_RPC_URL is not set
 */
import { describe, it, before, after } from "node:test"
import assert from "node:assert/strict"
import { initAndUnlock, runCli, createTestEnv } from "../helpers/setup.js"

const HAS_RPC = !!process.env.BSC_RPC_URL

describe("getBalance", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("returns JSON containing chain, chainId, eoaAddress fields", { skip: !HAS_RPC }, () => {
    const res = runCli(`balance --chain bsc --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    assert.ok("chain" in res.json, "missing chain field")
    assert.ok("chainId" in res.json, "missing chainId field")
    assert.ok("eoaAddress" in res.json, "missing eoaAddress field")
  })

  it("eoaAddress matches the address returned by init", { skip: !HAS_RPC }, () => {
    const res = runCli(`balance --chain bsc --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.equal(
      res.json.eoaAddress.toLowerCase(),
      ctx.address.toLowerCase(),
      "eoaAddress should match init address"
    )
  })

  it("returns balances object containing BNB/USDC/USDT/WBNB (BSC chain)", { skip: !HAS_RPC }, () => {
    const res = runCli(`balance --chain bsc --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json.balances, "missing balances object")
    // BSC chain should contain native token BNB and configured tokens
    const keys = Object.keys(res.json.balances)
    assert.ok(keys.some(k => k.includes("BNB")), "should contain BNB balance")
    assert.ok(keys.some(k => k.includes("USDC")), "should contain USDC balance")
    assert.ok(keys.some(k => k.includes("USDT")), "should contain USDT balance")
    assert.ok(keys.some(k => k.includes("WBNB")), "should contain WBNB balance")
  })

  it("new wallet has all token balances at 0", { skip: !HAS_RPC }, () => {
    const res = runCli(`balance --chain bsc --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    for (const [sym, val] of Object.entries(res.json.balances)) {
      assert.equal(val, "0", `${sym} balance should be 0, actual: ${val}`)
    }
  })

  it("querying by numeric ID (56) gives same result as chain name (bsc)", { skip: !HAS_RPC }, () => {
    const byName = runCli(`balance --chain bsc --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    const byId = runCli(`balance --chain 56 --token ${ctx.token}`, ctx.env, { timeout: 60_000 })
    assert.equal(byName.exitCode, 0, `query by name failed: ${byName.stderr}`)
    assert.equal(byId.exitCode, 0, `query by ID failed: ${byId.stderr}`)
    assert.equal(byName.json.chainId, byId.json.chainId, "chainId should be the same")
    assert.equal(
      byName.json.eoaAddress.toLowerCase(),
      byId.json.eoaAddress.toLowerCase(),
      "eoaAddress should be the same"
    )
  })

  it("requires a session token with read permission", { skip: !HAS_RPC }, () => {
    // Using invalid token should fail
    const res = runCli("balance --chain bsc --token invalid_token_xxx", ctx.env, { timeout: 60_000 })
    assert.notEqual(res.exitCode, 0, "invalid token should result in non-zero exit code")
  })

  it("invalid token is rejected", { skip: !HAS_RPC }, () => {
    const res = runCli("balance --chain bsc --token bogus_session_id", ctx.env, { timeout: 60_000 })
    assert.notEqual(res.exitCode, 0, "forged token should be rejected")
    const output = res.stderr || res.stdout
    assert.ok(output.length > 0, "should output error message")
  })
})

describe("getTxStatus", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("returns { status: \"pending\" } for a nonexistent transaction hash", { skip: !HAS_RPC }, () => {
    // Use a fake hash that cannot possibly exist
    const fakeHash = "0x" + "ab".repeat(32)
    const res = runCli(`tx-status --chain bsc --hash ${fakeHash}`, ctx.env, { timeout: 60_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    assert.equal(res.json.status, "pending", "nonexistent transaction should return pending status")
  })
})

describe("getPortfolio", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("returns a chains array containing results for multiple chains", { skip: !HAS_RPC }, () => {
    const res = runCli(`portfolio --token ${ctx.token}`, ctx.env, { timeout: 120_000 })
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    assert.ok(Array.isArray(res.json.chains), "chains should be an array")
    assert.ok(res.json.chains.length > 1, "should contain results for multiple chains")
  })
})

describe("getAllowances", () => {
  let ctx

  before(() => {
    ctx = initAndUnlock()
  })

  after(() => {
    ctx?.cleanup()
  })

  it("returns allowance amount for specified token + spender", { skip: !HAS_RPC }, () => {
    // Use a random address as spender
    const spender = "0x0000000000000000000000000000000000000001"
    const res = runCli(
      `allowances --chain bsc --token ${ctx.token} --asset USDC --spender ${spender}`,
      ctx.env,
      { timeout: 60_000 }
    )
    assert.equal(res.exitCode, 0, `CLI failed: ${res.stderr}`)
    assert.ok(res.json, "should return valid JSON")
    assert.ok("allowances" in res.json, "should contain allowances field")
    assert.ok(Array.isArray(res.json.allowances), "allowances should be an array")
  })
})
