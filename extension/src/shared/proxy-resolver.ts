/**
 * Polymarket proxy address derivation.
 *
 * Polymarket users hold their CTF positions in a "proxy" contract derived
 * deterministically from their EOA. There are two proxy types depending on how
 * they signed up to Polymarket:
 *
 *   1. Safe (Gnosis Safe v1.3) — used when the user logs in with MetaMask /
 *      WalletConnect / external wallet. Signing uses the EOA directly.
 *   2. Magic — used when the user logs in with email / Google / Apple. Signing
 *      key is in Magic's HSM, but Polymarket exposes "Export Private Key", so
 *      the user can move the key to MetaMask and reach feature parity with #1.
 *
 * Both formulas are CREATE2 with constants verified against ground-truth
 * fixtures (see proxy-resolver.test.ts):
 *
 *   Safe : 0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1 → 0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E
 *   Magic: 0x917c7378f3F9aAfFa29e1A92c726ef9bfb6378D4 → 0xa048278D51D83b3640065BeBDe8B82C4DdbDbe26
 */

import { keccak_256 } from "@noble/hashes/sha3.js";

// -------------------------------------------------------------- known addresses

export const POLYGON_CHAIN_ID = 137;

/** Polymarket's own SafeProxyFactory (NOT the canonical Safe v1.3 factory). */
export const PM_SAFE_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b";
/** Safe v1.3.0 singleton (master copy) used as Safe proxy implementation. */
export const PM_SAFE_MASTER_COPY = "0xE51abdf814f8854941b9Fe8e3A4F65CAB4e7A4a8";
/** Polymarket's custom Safe fallback handler (not used in salt; here for reference). */
export const PM_SAFE_FALLBACK_HANDLER =
  "0xe16bA5bF81E5BB113e4752E4fdC20351d796fB24";

/** Polymarket Magic-link ProxyWalletFactory. */
export const PM_MAGIC_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052";
/** Current Magic proxy implementation (live value of factory.getImplementation()). */
export const PM_MAGIC_IMPL = "0x44e999d5c2f66ef0861317f9a4805ac2e90aeb4f";

/** Polymarket ConditionalTokens (ERC-1155). */
export const POLYMARKET_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";

/**
 * Polymarket Safe factory's `proxyCreationCode()` view return — 369 bytes.
 * Read once via `cast call $factory "proxyCreationCode()(bytes)"` and pinned
 * here for offline derivation. If Polymarket ever upgrades this code, all
 * derived Safe addresses will diverge — see the validate-proxies smoke test
 * in proxy-resolver.test.ts which would catch this immediately.
 */
const PM_SAFE_PROXY_CREATION_CODE_HEX =
  "608060405234801561001057600080fd5b5060405161017138038061017183398101604081905261002f916100b9565b6001600160a01b0381166100945760405162461bcd60e51b815260206004820152602260248201527f496e76616c69642073696e676c65746f6e20616464726573732070726f766964604482015261195960f21b606482015260840160405180910390fd5b600080546001600160a01b0319166001600160a01b03929092169190911790556100e7565b6000602082840312156100ca578081fd5b81516001600160a01b03811681146100e0578182fd5b9392505050565b607c806100f56000396000f3fe6080604052600080546001600160a01b0316813563530ca43760e11b1415602857808252602082f35b3682833781823684845af490503d82833e806041573d82fd5b503d81f3fea264697066735822122015938e3bf2c49f5df5c1b7f9569fa85cc5d6f3074bb258a2dc0c7e299bc9e33664736f6c63430008040033";

// -------------------------------------------------------------- low-level utils

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
  if (clean.length % 2 !== 0) throw new Error(`odd-length hex: ${hex}`);
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function bytesToHex(bytes: Uint8Array): string {
  let s = "0x";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

function concat(...arrs: Uint8Array[]): Uint8Array {
  const total = arrs.reduce((n, a) => n + a.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const a of arrs) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}

/** ABI-encode a single address as a 32-byte left-padded word. */
function encodeAddress(addr: string): Uint8Array {
  const bytes = hexToBytes(addr);
  if (bytes.length !== 20) throw new Error(`bad address: ${addr}`);
  const out = new Uint8Array(32);
  out.set(bytes, 12);
  return out;
}

/** EIP-55 checksum case for a 20-byte address. */
export function toChecksumAddress(addr: string): string {
  const lower = addr.toLowerCase().replace(/^0x/, "");
  if (lower.length !== 40) throw new Error(`bad address: ${addr}`);
  const hash = keccak_256(new TextEncoder().encode(lower));
  let out = "0x";
  for (let i = 0; i < 40; i++) {
    const c = lower[i];
    if (c >= "0" && c <= "9") {
      out += c;
    } else {
      const nibble = hash[i >> 1] >> (i % 2 === 0 ? 4 : 0);
      out += (nibble & 0xf) >= 8 ? c.toUpperCase() : c;
    }
  }
  return out;
}

function create2(factory: string, salt: Uint8Array, initCode: Uint8Array): string {
  const initHash = keccak_256(initCode);
  const buf = concat(new Uint8Array([0xff]), hexToBytes(factory), salt, initHash);
  const hash = keccak_256(buf);
  return toChecksumAddress(bytesToHex(hash.slice(12)));
}

// -------------------------------------------------------------- Safe path

/**
 * Build the 32-byte salt for a Safe proxy.
 *   salt = keccak256(abi.encode(eoa))
 * abi.encode of a single address pads to 32 bytes (12 leading zeros + 20 bytes).
 */
function safeSalt(eoa: string): Uint8Array {
  return keccak_256(encodeAddress(eoa));
}

/**
 * Build the Safe initCode = proxyCreationCode || abi.encode(masterCopy).
 * The masterCopy is appended as 32-byte ABI-encoded address.
 */
function safeInitCode(masterCopy: string = PM_SAFE_MASTER_COPY): Uint8Array {
  return concat(
    hexToBytes(PM_SAFE_PROXY_CREATION_CODE_HEX),
    encodeAddress(masterCopy),
  );
}

/** Compute the Polymarket Safe proxy address for a given EOA. */
export function safeProxyOf(eoa: string): string {
  return create2(PM_SAFE_FACTORY, safeSalt(eoa), safeInitCode());
}

// -------------------------------------------------------------- Magic path

const MAGIC_PREFIX = "3d3d606380380380913d393d73"; // 13 bytes
const MAGIC_MID = "5af4602a57600080fd5b602d8060366000396000f3363d3d373d3d3d363d73"; // 31 bytes
const MAGIC_SUFFIX = "5af43d82803e903d91602b57fd5bf3"; // 15 bytes

/** Selector for cloneConstructor(bytes) — 0x52e831dd. */
const CLONE_CONSTRUCTOR_CALLDATA = (() => {
  const sel = keccak_256(
    new TextEncoder().encode("cloneConstructor(bytes)"),
  ).slice(0, 4);
  // abi.encode(empty bytes) = offset(0x20) || length(0x00) — both as uint256
  const offset = new Uint8Array(32);
  offset[31] = 0x20;
  const length = new Uint8Array(32);
  return concat(sel, offset, length);
})();

/**
 * Build the 167-byte Magic initCode following ProxyWalletLib.computeCreationCode
 * exactly (verified against fixture). Layout:
 *   [0-12]   prefix (13B)
 *   [13-32]  factory deployer (20B)
 *   [33-63]  mid (31B)
 *   [64-83]  implementation (20B)
 *   [84-98]  suffix (15B)
 *   [99-166] cloneConstructor(bytes) calldata (68B)
 */
function magicInitCode(
  factory: string = PM_MAGIC_FACTORY,
  impl: string = PM_MAGIC_IMPL,
): Uint8Array {
  return concat(
    hexToBytes(MAGIC_PREFIX),
    hexToBytes(factory),
    hexToBytes(MAGIC_MID),
    hexToBytes(impl),
    hexToBytes(MAGIC_SUFFIX),
    CLONE_CONSTRUCTOR_CALLDATA,
  );
}

/** Salt for a Magic proxy: keccak256(abi.encodePacked(eoa)) — note PACKED, 20 bytes. */
function magicSalt(eoa: string): Uint8Array {
  return keccak_256(hexToBytes(eoa));
}

/** Compute the Polymarket Magic proxy address for a given EOA. */
export function magicProxyOf(eoa: string): string {
  return create2(PM_MAGIC_FACTORY, magicSalt(eoa), magicInitCode());
}

// -------------------------------------------------------------- Detection

export type ProxyKind = "safe" | "magic" | "none";

export interface ProxyCandidate {
  kind: "safe" | "magic";
  address: string;
  /** True if a contract is deployed at the address. */
  deployed: boolean;
}

export interface ProxyDetectionResult {
  /** Best guess for the user's active proxy type, by deployment status. */
  kind: ProxyKind;
  /** The active proxy address (deployed contract), or null if neither exists. */
  proxy: string | null;
  candidates: ProxyCandidate[];
}

/**
 * Probe both Safe and Magic derived addresses on-chain. Whichever has bytecode
 * is the user's active Polymarket proxy.
 *
 * Note: Polymarket Safes are deployed lazily on first trade. A user who has
 * NEVER traded on Polymarket will get `kind: "none"` even if their EOA is a
 * valid signer.
 */
export async function detectProxy(
  eoa: string,
  rpcUrl: string,
): Promise<ProxyDetectionResult> {
  const safe = safeProxyOf(eoa);
  const magic = magicProxyOf(eoa);

  const [safeDeployed, magicDeployed] = await Promise.all([
    addressHasCode(rpcUrl, safe),
    addressHasCode(rpcUrl, magic),
  ]);

  const candidates: ProxyCandidate[] = [
    { kind: "safe", address: safe, deployed: safeDeployed },
    { kind: "magic", address: magic, deployed: magicDeployed },
  ];

  if (safeDeployed) return { kind: "safe", proxy: safe, candidates };
  if (magicDeployed) return { kind: "magic", proxy: magic, candidates };
  return { kind: "none", proxy: null, candidates };
}

async function addressHasCode(rpcUrl: string, address: string): Promise<boolean> {
  const resp = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "eth_getCode",
      params: [address.toLowerCase(), "latest"],
    }),
  });
  if (!resp.ok) throw new Error(`eth_getCode failed: ${resp.status}`);
  const json = (await resp.json()) as { result?: string; error?: { message: string } };
  if (json.error) throw new Error(json.error.message);
  return typeof json.result === "string" && json.result !== "0x";
}
