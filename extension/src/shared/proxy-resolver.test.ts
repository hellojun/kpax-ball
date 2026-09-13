/**
 * Ground-truth fixtures: real EOA → proxy mappings observed on Polygon mainnet.
 * If these tests ever fail, Polymarket has changed a factory / impl / fallback
 * handler and the constants in proxy-resolver.ts need updating.
 */

import { describe, expect, it } from "vitest";
import {
  PM_MAGIC_FACTORY,
  PM_MAGIC_IMPL,
  PM_SAFE_FACTORY,
  PM_SAFE_FALLBACK_HANDLER,
  PM_SAFE_MASTER_COPY,
  magicProxyOf,
  safeProxyOf,
  toChecksumAddress,
} from "./proxy-resolver";

describe("Polymarket proxy derivation", () => {
  it("Safe proxy from external-wallet EOA", () => {
    const eoa = "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1";
    const expected = "0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E";
    expect(safeProxyOf(eoa)).toBe(expected);
  });

  it("Magic proxy from Magic-link EOA", () => {
    const eoa = "0x917c7378f3F9aAfFa29e1A92c726ef9bfb6378D4";
    const expected = "0xa048278D51D83b3640065BeBDe8B82C4DdbDbe26";
    expect(magicProxyOf(eoa)).toBe(expected);
  });

  it("derivation is deterministic and case-insensitive", () => {
    const lower = "0xaa35e5045783e2f9ca553cad9545baf213998ab1";
    const mixed = "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1";
    expect(safeProxyOf(lower)).toBe(safeProxyOf(mixed));
    expect(magicProxyOf(lower)).toBe(magicProxyOf(mixed));
  });

  it("constants are sane addresses", () => {
    for (const a of [
      PM_SAFE_FACTORY,
      PM_SAFE_MASTER_COPY,
      PM_SAFE_FALLBACK_HANDLER,
      PM_MAGIC_FACTORY,
      PM_MAGIC_IMPL,
    ]) {
      expect(a).toMatch(/^0x[0-9a-fA-F]{40}$/);
    }
  });
});

describe("toChecksumAddress (EIP-55)", () => {
  // Cases from EIP-55 spec.
  it.each([
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
  ])("%s round-trips", (canonical) => {
    expect(toChecksumAddress(canonical.toLowerCase())).toBe(canonical);
  });
});
