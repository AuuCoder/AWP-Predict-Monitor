import { createWalletClient, http, getAddress as checksumAddr, serializeTransaction } from "viem"
import { loadSigner } from "./keystore.js"
import { viemChain, publicClient, resolveChainId, getRpcUrl } from "./chains.js"
import { logTransaction } from "./tx-logger.js"

// Build transaction parameters with auto-estimation
async function buildTxParams({ to, value, data, gas, nonce, chain }) {
  const chainId = resolveChainId(chain)
  const client = publicClient(chainId)
  const { account: signer } = loadSigner()

  const txParams = {
    to: checksumAddr(to),
    value: BigInt(value || "0"),
    data: data || "0x",
    account: signer,
    chain: viemChain(chainId),
  }

  // Auto-estimate gas if not provided
  if (gas) {
    txParams.gas = BigInt(gas)
  } else {
    txParams.gas = await client.estimateGas({
      account: signer.address,
      to: txParams.to,
      value: txParams.value,
      data: txParams.data,
    })
  }

  // Auto-fetch nonce if not provided
  if (nonce !== undefined && nonce !== null) {
    txParams.nonce = parseInt(nonce)
  }

  // Get fee parameters
  try {
    const block = await client.getBlock()
    const baseFee = block.baseFeePerGas > 0n ? block.baseFeePerGas : await client.getGasPrice()
    txParams.maxFeePerGas = baseFee * 2n
    txParams.maxPriorityFeePerGas = baseFee / 10n || 1n
  } catch {
    const gasPrice = await client.getGasPrice()
    txParams.maxFeePerGas = gasPrice * 2n
    txParams.maxPriorityFeePerGas = gasPrice / 10n || 1n
  }

  return { txParams, signer, chainId }
}

// Sign transaction without broadcasting — returns signed raw hex
export async function signRawTx({ to, value, data, gas, nonce, chain }) {
  const chainId = resolveChainId(chain)
  const { account: signer } = loadSigner()
  const client = publicClient(chainId)
  const chainObj = viemChain(chainId)

  const walletClient = createWalletClient({
    account: signer,
    chain: chainObj,
    transport: http(getRpcUrl(chainId)),
  })

  const txRequest = {
    to: checksumAddr(to),
    value: BigInt(value || "0"),
    data: data || "0x",
  }

  // Gas limit
  if (gas) {
    txRequest.gas = BigInt(gas)
  } else {
    txRequest.gas = await client.estimateGas({
      account: signer.address, ...txRequest,
    })
  }

  // Nonce
  if (nonce !== undefined && nonce !== null) {
    txRequest.nonce = parseInt(nonce)
  } else {
    txRequest.nonce = await client.getTransactionCount({ address: signer.address })
  }

  // Fee params
  try {
    const block = await client.getBlock()
    const baseFee = block.baseFeePerGas > 0n ? block.baseFeePerGas : await client.getGasPrice()
    txRequest.maxFeePerGas = baseFee * 2n
    txRequest.maxPriorityFeePerGas = baseFee / 10n || 1n
  } catch {
    const gasPrice = await client.getGasPrice()
    txRequest.maxFeePerGas = gasPrice * 2n
    txRequest.maxPriorityFeePerGas = gasPrice / 10n || 1n
  }

  txRequest.chainId = chainId
  txRequest.type = "eip1559"

  // Sign using viem's signTransaction
  const signedTx = await walletClient.signTransaction(txRequest)

  return {
    status: "signed",
    signedTx,
    from: signer.address,
    to: txRequest.to,
    value: txRequest.value.toString(),
    data: txRequest.data,
    gas: txRequest.gas.toString(),
    nonce: txRequest.nonce,
    chainId,
    chain: chainObj.name,
  }
}

// Sign and broadcast — returns tx hash + receipt
export async function sendRawTx({ to, value, data, gas, nonce, chain }) {
  const chainId = resolveChainId(chain)
  const chainObj = viemChain(chainId)
  const { account: signer } = loadSigner()
  const client = publicClient(chainId)

  const walletClient = createWalletClient({
    account: signer,
    chain: chainObj,
    transport: http(getRpcUrl(chainId)),
  })

  const txRequest = {
    to: checksumAddr(to),
    value: BigInt(value || "0"),
    data: data || "0x",
  }

  if (gas) txRequest.gas = BigInt(gas)
  if (nonce !== undefined && nonce !== null) txRequest.nonce = parseInt(nonce)

  const hash = await walletClient.sendTransaction(txRequest)

  const receipt = await client.waitForTransactionReceipt({
    hash, timeout: 120_000, confirmations: 1,
  })

  const result = {
    status: receipt.status === "success" ? "sent" : "reverted",
    mode: "direct",
    txHash: hash,
    from: signer.address,
    to: txRequest.to,
    value: txRequest.value.toString(),
    data: txRequest.data,
    chain: chainObj.name,
    chainId,
    gasUsed: receipt.gasUsed.toString(),
    blockNumber: Number(receipt.blockNumber),
  }

  await logTransaction({ ...result, type: "raw_tx" })
  return result
}
