/**
 * signing.js integration tests
 * Tests message signing, typed data signing, and CLI permission control
 */
import { describe, it, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { join } from "node:path"
import {
  createTestEnv, runCli, initAndUnlock, PROJECT_ROOT,
} from "../helpers/setup.js"

/**
 * Execute signing functions in an isolated environment, returns JSON result
 */
function execSigning(home, code) {
  const script = `
    process.env.HOME = ${JSON.stringify(home)};
    ${code}
  `
  const out = execFileSync("node", ["--input-type=module", "-e", script], {
    env: { ...process.env, HOME: home, WALLET_PASSWORD: "test-pwd-123" },
    cwd: PROJECT_ROOT,
    encoding: "utf8",
    timeout: 30_000,
  }).trim()
  return out ? JSON.parse(out) : null
}

// EIP-712 typed data example
const TYPED_DATA_CODE = `{
  domain: {
    name: "Test",
    version: "1",
    chainId: 1,
    verifyingContract: "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
  },
  types: {
    Person: [
      { name: "name", type: "string" },
      { name: "wallet", type: "address" },
    ],
  },
  primaryType: "Person",
  message: {
    name: "Alice",
    wallet: "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
  },
}`

describe("signing", () => {
  let ctx

  beforeEach(() => {
    ctx = initAndUnlock()
  })

  afterEach(() => {
    ctx.cleanup()
  })

  // ---- signMessage ----

  it("signMessage: returns { signature, signer }", () => {
    const result = execSigning(ctx.home, `
      import { signMessage } from "./scripts/lib/signing.js";
      const res = await signMessage("hello world");
      console.log(JSON.stringify(res));
    `)
    assert.ok(result.signature, "should have signature field")
    assert.ok(result.signer, "should have signer field")
  })

  it("signMessage: signature is a valid hex string starting with 0x", () => {
    const result = execSigning(ctx.home, `
      import { signMessage } from "./scripts/lib/signing.js";
      const res = await signMessage("test message");
      console.log(JSON.stringify(res));
    `)
    assert.ok(result.signature.startsWith("0x"), "signature should start with 0x")
    // After removing 0x, should be all hex characters
    const hexPart = result.signature.slice(2)
    assert.match(hexPart, /^[0-9a-fA-F]+$/, "signature should be valid hex")
    // ECDSA signature is 65 bytes = 130 hex characters
    assert.equal(hexPart.length, 130, "ECDSA signature should be 65 bytes (130 hex characters)")
  })

  it("signMessage: signer matches wallet address", () => {
    const result = execSigning(ctx.home, `
      import { signMessage } from "./scripts/lib/signing.js";
      import { getAddress } from "./scripts/lib/keystore.js";
      const res = await signMessage("verify signer");
      const walletAddr = getAddress("eoa");
      console.log(JSON.stringify({ ...res, walletAddr }));
    `)
    assert.equal(result.signer.toLowerCase(), result.walletAddr.toLowerCase(),
      "signer should match wallet EOA address")
  })

  it("signMessage: different messages produce different signatures", () => {
    const result = execSigning(ctx.home, `
      import { signMessage } from "./scripts/lib/signing.js";
      const r1 = await signMessage("message A");
      const r2 = await signMessage("message B");
      console.log(JSON.stringify({ sig1: r1.signature, sig2: r2.signature }));
    `)
    assert.notEqual(result.sig1, result.sig2, "different messages should produce different signatures")
  })

  it("signMessage: same message produces same signature (deterministic)", () => {
    const result = execSigning(ctx.home, `
      import { signMessage } from "./scripts/lib/signing.js";
      const r1 = await signMessage("deterministic test");
      const r2 = await signMessage("deterministic test");
      console.log(JSON.stringify({ sig1: r1.signature, sig2: r2.signature }));
    `)
    assert.equal(result.sig1, result.sig2, "same message should produce same signature")
  })

  // ---- signTypedData ----

  it("signTypedData: returns { signature, signer }", () => {
    const result = execSigning(ctx.home, `
      import { signTypedData } from "./scripts/lib/signing.js";
      const typedData = ${TYPED_DATA_CODE};
      const res = await signTypedData(typedData);
      console.log(JSON.stringify(res));
    `)
    assert.ok(result.signature, "should have signature field")
    assert.ok(result.signer, "should have signer field")
  })

  it("signTypedData: signature format is valid hex", () => {
    const result = execSigning(ctx.home, `
      import { signTypedData } from "./scripts/lib/signing.js";
      const typedData = ${TYPED_DATA_CODE};
      const res = await signTypedData(typedData);
      console.log(JSON.stringify(res));
    `)
    assert.ok(result.signature.startsWith("0x"), "signature should start with 0x")
    const hexPart = result.signature.slice(2)
    assert.match(hexPart, /^[0-9a-fA-F]+$/, "signature should be valid hex")
  })

  // ---- CLI sign-message: permission control ----

  it("sign-message CLI: session token with transfer scope can sign", () => {
    // initAndUnlock defaults to full scope (>= transfer), should be able to sign
    const res = runCli(
      `sign-message --token ${ctx.token} --message "hello from CLI"`,
      ctx.env,
    )
    assert.equal(res.exitCode, 0, `expected success, actual stderr: ${res.stderr}`)
    assert.ok(res.json.signature, "should return signature")
    assert.ok(res.json.signer, "should return signer")
  })

  it("sign-message CLI: read-only scope is rejected", () => {
    // Create a session with read permission
    const readUnlock = runCli("unlock --duration 3600 --scope read", ctx.env)
    assert.equal(readUnlock.exitCode, 0)
    const readToken = readUnlock.json.sessionToken

    const res = runCli(
      `sign-message --token ${readToken} --message "should fail"`,
      ctx.env,
    )
    assert.notEqual(res.exitCode, 0, "read scope should be rejected")
    const output = res.stderr + res.stdout
    assert.ok(output.includes("insufficient") || output.includes("Scope"),
      `expected insufficient permission error, actual: ${output}`)
  })
})
