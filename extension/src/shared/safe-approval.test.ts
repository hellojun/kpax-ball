import { describe, expect, it, vi } from "vitest";
import { hashTypedData } from "viem";

import {
  POLYGON_CHAIN_ID,
  buildApprovalSafeTx,
  buildExecTransactionData,
  buildSafeTypedData,
  computeSafeTxHash,
  isVaultApproved,
  readSafeNonce,
  type Signer,
} from "./safe-approval";

const SAFE = "0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E" as const;
const CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045" as const;
const VAULT = "0x000000000000000000000000000000000000dEaD" as const;
const OWNER = "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1" as const;

describe("buildApprovalSafeTx", () => {
  it("encodes setApprovalForAll(vault, true) on the CTF", () => {
    const tx = buildApprovalSafeTx(VAULT, 7n);
    expect(tx.to).toBe(CTF);
    expect(tx.value).toBe(0n);
    expect(tx.operation).toBe(0);
    expect(tx.nonce).toBe(7n);

    // setApprovalForAll selector = keccak("setApprovalForAll(address,bool)")[:4]
    expect(tx.data.startsWith("0xa22cb465")).toBe(true);
    // operator = vault, approved = 1 (32-byte right-padded args)
    expect(tx.data.toLowerCase()).toContain(VAULT.slice(2).toLowerCase());
    expect(tx.data.endsWith("1")).toBe(true); // last byte = 0x01 for true
  });
});

describe("computeSafeTxHash matches viem.hashTypedData", () => {
  it("produces the same digest as viem's reference implementation", () => {
    const tx = buildApprovalSafeTx(VAULT, 0n);
    const typedData = buildSafeTypedData(SAFE, POLYGON_CHAIN_ID, tx);

    const reference = hashTypedData({
      domain: typedData.domain,
      types: { SafeTx: typedData.types.SafeTx },
      primaryType: "SafeTx",
      message: typedData.message as unknown as Record<string, unknown>,
    });
    const ours = computeSafeTxHash(SAFE, POLYGON_CHAIN_ID, tx);
    expect(ours).toBe(reference);
  });

  it("is sensitive to nonce changes", () => {
    const tx0 = buildApprovalSafeTx(VAULT, 0n);
    const tx1 = buildApprovalSafeTx(VAULT, 1n);
    expect(
      computeSafeTxHash(SAFE, POLYGON_CHAIN_ID, tx0),
    ).not.toBe(computeSafeTxHash(SAFE, POLYGON_CHAIN_ID, tx1));
  });
});

describe("buildExecTransactionData", () => {
  it("encodes the 10-arg execTransaction call with the supplied signature", () => {
    const tx = buildApprovalSafeTx(VAULT, 0n);
    // 65-byte fake signature
    const sig =
      "0x" + "ab".repeat(64) + "1c";
    const data = buildExecTransactionData(tx, sig as `0x${string}`);
    // execTransaction selector = keccak("execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)")[:4]
    expect(data.startsWith("0x6a761202")).toBe(true);
    // signature must appear in the encoded calldata
    expect(data.toLowerCase()).toContain("ab".repeat(64));
  });
});

// ---------- mock signer for I/O paths ----------

function mockSigner(rpcResponses: Record<string, unknown>): Signer {
  return {
    address: OWNER,
    getChainId: vi.fn(async () => POLYGON_CHAIN_ID),
    signTypedData: vi.fn(async () => "0xdeadbeef" as `0x${string}`),
    sendTransaction: vi.fn(async () => "0xabcdef" as `0x${string}`),
    callRpc: vi.fn(async (method, params) => {
      const key = `${method}:${JSON.stringify(params)}`;
      if (key in rpcResponses) return rpcResponses[key] as never;
      // fall back to method-only key
      if (method in rpcResponses) return rpcResponses[method] as never;
      throw new Error(`unmocked rpc: ${key}`);
    }),
  };
}

describe("isVaultApproved", () => {
  it("returns true when bool=1", async () => {
    const signer = mockSigner({
      eth_call:
        "0x0000000000000000000000000000000000000000000000000000000000000001",
    });
    expect(await isVaultApproved(signer, SAFE, VAULT, CTF)).toBe(true);
  });

  it("returns false when bool=0", async () => {
    const signer = mockSigner({
      eth_call:
        "0x0000000000000000000000000000000000000000000000000000000000000000",
    });
    expect(await isVaultApproved(signer, SAFE, VAULT, CTF)).toBe(false);
  });
});

describe("readSafeNonce", () => {
  it("decodes a uint256 hex result", async () => {
    const signer = mockSigner({
      eth_call:
        "0x0000000000000000000000000000000000000000000000000000000000000005",
    });
    expect(await readSafeNonce(signer, SAFE)).toBe(5n);
  });
});
