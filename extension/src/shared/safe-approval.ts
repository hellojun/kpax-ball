/**
 * Safe v1.3 transaction helpers for the KPAX Lending approval flow.
 *
 * Polymarket users hold their CTF positions in a 1-of-1 Gnosis Safe v1.3 proxy
 * (the EOA they used to log in is the sole owner). To borrow against those
 * positions, the Safe must do a one-time `setApprovalForAll(KpaxVault, true)`
 * on the Polymarket ConditionalTokens contract. After that, KPAX's Vault
 * contract can pull collateral from the Safe directly during `openLoan`.
 *
 * This module:
 *   1. Reads `isApprovedForAll(safe, vault)` to skip the dance if already done
 *   2. Builds the Safe transaction calldata
 *   3. Computes the EIP-712 typed-data hash for the owner to sign
 *   4. Builds the `Safe.execTransaction(...)` calldata once we have a signature
 *
 * Wallet transport (signing + sending) is pluggable via the `Signer` interface,
 * so the side panel can use either an injected provider (MetaMask via content
 * script bridge) or WalletConnect.
 */

import {
  encodeFunctionData,
  encodeAbiParameters,
  keccak256,
  toBytes,
  type Address,
  type Hex,
} from "viem";

import { POLYMARKET_CTF } from "./proxy-resolver";
import { polygonReadRpc } from "./wallet";

// -------------------------------------------------------------- known

/** Polymarket's CTF (ERC-1155) — collateral-bearing token. */
export { POLYMARKET_CTF };

/** Polygon mainnet. */
export const POLYGON_CHAIN_ID = 137n;

/** Safe.execTransaction operation. We only need CALL (0); DELEGATECALL is 1. */
export const OPERATION_CALL = 0;

/** Safe v1.3 EIP-712 domain typehash =
 *  keccak256("EIP712Domain(uint256 chainId,address verifyingContract)")
 *  Note: Safe's domain has no name/version field. */
const DOMAIN_TYPEHASH: Hex =
  "0x47e79534a245952e8b16893a336b85a3d9ea9fa8c573f3d803afb92a79469218";

/** Safe v1.3 SafeTx struct typehash =
 *  keccak256("SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)")
 */
const SAFE_TX_TYPEHASH: Hex =
  "0xbb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8";

// -------------------------------------------------------------- types

/** Minimal Safe transaction (we only ever issue CALLs with no gas refund). */
export interface SafeTx {
  to: Address;
  value: bigint;
  data: Hex;
  operation: number;
  safeTxGas: bigint;
  baseGas: bigint;
  gasPrice: bigint;
  gasToken: Address;
  refundReceiver: Address;
  nonce: bigint;
}

/** Pluggable signer + RPC interface. The side panel will pass an
 *  implementation that proxies to the user's MetaMask via content-script
 *  bridge or to WalletConnect. */
export interface Signer {
  /** Address that will sign + broadcast (the Safe owner EOA). */
  address: Address;
  /** Returns the chain id this signer is on (decimal bigint). */
  getChainId(): Promise<bigint>;
  /** EIP-712 typed-data signature. Returns 0x-prefixed 65-byte hex. */
  signTypedData(typedData: SafeTypedData): Promise<Hex>;
  /** Send a transaction, return the tx hash. */
  sendTransaction(tx: { to: Address; data: Hex; value?: bigint }): Promise<Hex>;
  /** Read-only RPC: eth_call style. */
  callRpc<T = unknown>(method: string, params: unknown[]): Promise<T>;
}

/** Shape passed to wallet for `eth_signTypedData_v4`. */
export interface SafeTypedData {
  domain: { chainId: bigint; verifyingContract: Address };
  types: Record<string, Array<{ name: string; type: string }>>;
  primaryType: "SafeTx";
  message: SafeTx;
}

// -------------------------------------------------------------- ABIs

const ERC1155_ABI = [
  {
    name: "isApprovedForAll",
    type: "function",
    stateMutability: "view",
    inputs: [
      { name: "account", type: "address" },
      { name: "operator", type: "address" },
    ],
    outputs: [{ name: "", type: "bool" }],
  },
  {
    name: "setApprovalForAll",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "operator", type: "address" },
      { name: "approved", type: "bool" },
    ],
    outputs: [],
  },
] as const;

const SAFE_ABI = [
  {
    name: "nonce",
    type: "function",
    stateMutability: "view",
    inputs: [],
    outputs: [{ name: "", type: "uint256" }],
  },
  {
    name: "execTransaction",
    type: "function",
    stateMutability: "payable",
    inputs: [
      { name: "to", type: "address" },
      { name: "value", type: "uint256" },
      { name: "data", type: "bytes" },
      { name: "operation", type: "uint8" },
      { name: "safeTxGas", type: "uint256" },
      { name: "baseGas", type: "uint256" },
      { name: "gasPrice", type: "uint256" },
      { name: "gasToken", type: "address" },
      { name: "refundReceiver", type: "address" },
      { name: "signatures", type: "bytes" },
    ],
    outputs: [{ name: "success", type: "bool" }],
  },
] as const;

// -------------------------------------------------------------- read helpers

/** Returns true iff `vault` is currently approved as ERC-1155 operator on
 *  `ctf` for `safe`. Skips the approval dance if true.
 *
 *  Reads go via `polygonReadRpc` (publicnode) instead of `signer.callRpc`
 *  (MetaMask) because MM rate-limits eth_call when the user has many
 *  origins or no Infura key — which would then block the FOLLOWING
 *  signTypedData / sendTransaction in the same flow. The signer is no
 *  longer needed by these reads but we keep the param so callers don't
 *  have to thread separate plumbing. */
export async function isVaultApproved(
  _signer: Signer,
  safe: Address,
  vault: Address,
  ctf: Address = POLYMARKET_CTF as Address,
): Promise<boolean> {
  const data = encodeFunctionData({
    abi: ERC1155_ABI,
    functionName: "isApprovedForAll",
    args: [safe, vault],
  });
  const result = await polygonReadRpc<Hex>("eth_call", [
    { to: ctf, data },
    "latest",
  ]);
  return result.length >= 66 && result.slice(-1) === "1";
}

/** Read the current Safe nonce (needed when constructing a SafeTx). */
export async function readSafeNonce(
  _signer: Signer,
  safe: Address,
): Promise<bigint> {
  const data = encodeFunctionData({ abi: SAFE_ABI, functionName: "nonce" });
  const result = await polygonReadRpc<Hex>("eth_call", [
    { to: safe, data },
    "latest",
  ]);
  return BigInt(result);
}

// -------------------------------------------------------------- builders

/** Build a SafeTx that calls `setApprovalForAll(vault, true)` on the CTF. */
export function buildApprovalSafeTx(
  vault: Address,
  nonce: bigint,
  ctf: Address = POLYMARKET_CTF as Address,
): SafeTx {
  const data = encodeFunctionData({
    abi: ERC1155_ABI,
    functionName: "setApprovalForAll",
    args: [vault, true],
  });
  return {
    to: ctf,
    value: 0n,
    data,
    operation: OPERATION_CALL,
    safeTxGas: 0n,
    baseGas: 0n,
    gasPrice: 0n,
    gasToken: "0x0000000000000000000000000000000000000000",
    refundReceiver: "0x0000000000000000000000000000000000000000",
    nonce,
  };
}

/** Build the EIP-712 typed-data payload to ship to the wallet for signing. */
export function buildSafeTypedData(
  safe: Address,
  chainId: bigint,
  tx: SafeTx,
): SafeTypedData {
  return {
    domain: { chainId, verifyingContract: safe },
    types: {
      EIP712Domain: [
        { name: "chainId", type: "uint256" },
        { name: "verifyingContract", type: "address" },
      ],
      SafeTx: [
        { name: "to", type: "address" },
        { name: "value", type: "uint256" },
        { name: "data", type: "bytes" },
        { name: "operation", type: "uint8" },
        { name: "safeTxGas", type: "uint256" },
        { name: "baseGas", type: "uint256" },
        { name: "gasPrice", type: "uint256" },
        { name: "gasToken", type: "address" },
        { name: "refundReceiver", type: "address" },
        { name: "nonce", type: "uint256" },
      ],
    },
    primaryType: "SafeTx",
    message: tx,
  };
}

// -------------------------------------------------------------- hashing

/** Compute the EIP-712 digest a Safe owner must sign. Useful for tests +
 *  client-side sanity. The wallet computes its own version when signing. */
export function computeSafeTxHash(
  safe: Address,
  chainId: bigint,
  tx: SafeTx,
): Hex {
  const domainSeparator = keccak256(
    encodeAbiParameters(
      [
        { type: "bytes32" },
        { type: "uint256" },
        { type: "address" },
      ],
      [DOMAIN_TYPEHASH, chainId, safe],
    ),
  );

  const safeTxHash = keccak256(
    encodeAbiParameters(
      [
        { type: "bytes32" },
        { type: "address" },
        { type: "uint256" },
        { type: "bytes32" },
        { type: "uint8" },
        { type: "uint256" },
        { type: "uint256" },
        { type: "uint256" },
        { type: "address" },
        { type: "address" },
        { type: "uint256" },
      ],
      [
        SAFE_TX_TYPEHASH,
        tx.to,
        tx.value,
        keccak256(tx.data),
        tx.operation,
        tx.safeTxGas,
        tx.baseGas,
        tx.gasPrice,
        tx.gasToken,
        tx.refundReceiver,
        tx.nonce,
      ],
    ),
  );

  // 0x19 0x01 || domainSeparator || safeTxHash
  const prefix = new Uint8Array([0x19, 0x01]);
  const ds = toBytes(domainSeparator);
  const sh = toBytes(safeTxHash);
  const combined = new Uint8Array(prefix.length + ds.length + sh.length);
  combined.set(prefix, 0);
  combined.set(ds, prefix.length);
  combined.set(sh, prefix.length + ds.length);
  return keccak256(combined);
}

// -------------------------------------------------------------- execution

/** Build the calldata for `Safe.execTransaction(...)` with the supplied owner
 *  signature. Side panel sends this calldata to the Safe address via the
 *  user's wallet. */
export function buildExecTransactionData(tx: SafeTx, signature: Hex): Hex {
  return encodeFunctionData({
    abi: SAFE_ABI,
    functionName: "execTransaction",
    args: [
      tx.to,
      tx.value,
      tx.data,
      tx.operation,
      tx.safeTxGas,
      tx.baseGas,
      tx.gasPrice,
      tx.gasToken,
      tx.refundReceiver,
      signature,
    ],
  });
}

// -------------------------------------------------------------- top-level orchestration

export interface ApprovalResult {
  /** Tx hash of the broadcast `Safe.execTransaction` call. */
  txHash: Hex;
  /** The SafeTx that was signed + executed (useful for UI). */
  safeTx: SafeTx;
  /** EIP-712 digest the owner signed (useful for debugging). */
  safeTxHash: Hex;
  /** The 65-byte ECDSA signature the owner produced. */
  signature: Hex;
}

/** End-to-end approval: detect status, sign, execute. Skips signing entirely
 *  if the vault is already approved. */
export async function approveVaultIfNeeded(
  signer: Signer,
  safe: Address,
  vault: Address,
  ctf: Address = POLYMARKET_CTF as Address,
): Promise<ApprovalResult | null> {
  const already = await isVaultApproved(signer, safe, vault, ctf);
  if (already) return null;

  const [chainId, nonce] = await Promise.all([
    signer.getChainId(),
    readSafeNonce(signer, safe),
  ]);

  const safeTx = buildApprovalSafeTx(vault, nonce, ctf);
  const typedData = buildSafeTypedData(safe, chainId, safeTx);
  const signature = await signer.signTypedData(typedData);

  const data = buildExecTransactionData(safeTx, signature);
  const txHash = await signer.sendTransaction({ to: safe, data });

  return {
    txHash,
    safeTx,
    safeTxHash: computeSafeTxHash(safe, chainId, safeTx),
    signature,
  };
}
