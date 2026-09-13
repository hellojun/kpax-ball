"""Polymarket proxy address derivation (Python port of
extension/src/shared/proxy-resolver.ts).

Two formulas, both verified against ground-truth fixtures (see test):

  Safe : 0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1 → 0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E
  Magic: 0x917c7378f3F9aAfFa29e1A92c726ef9bfb6378D4 → 0xa048278D51D83b3640065BeBDe8B82C4DdbDbe26

Use the same import path on both sides: TS imports from `@shared/proxy-resolver`
in extension; Python imports `app.services.lending.proxy_resolver` here. Keep
the constants identical between files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from eth_hash.auto import keccak

logger = logging.getLogger(__name__)

# -------------------------------------------------------------- known addresses

POLYGON_CHAIN_ID = 137

PM_SAFE_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
PM_SAFE_MASTER_COPY = "0xE51abdf814f8854941b9Fe8e3A4F65CAB4e7A4a8"
PM_SAFE_FALLBACK_HANDLER = "0xe16bA5bF81E5BB113e4752E4fdC20351d796fB24"

PM_MAGIC_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
PM_MAGIC_IMPL = "0x44e999d5c2f66ef0861317f9a4805ac2e90aeb4f"

POLYMARKET_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# 369-byte Safe proxyCreationCode pinned from chain — see TS file for context.
PM_SAFE_PROXY_CREATION_CODE_HEX = (
    "608060405234801561001057600080fd5b5060405161017138038061017183398101604081"
    "905261002f916100b9565b6001600160a01b0381166100945760405162461bcd60e51b8152"
    "60206004820152602260248201527f496e76616c69642073696e676c65746f6e2061646472"
    "6573732070726f766964604482015261195960f21b606482015260840160405180910390fd"
    "5b600080546001600160a01b0319166001600160a01b0392909216919091179055610"
    "0e7565b6000602082840312156100ca578081fd5b81516001600160a01b0381168114"
    "6100e0578182fd5b9392505050565b607c806100f56000396000f3fe60806040526000805"
    "46001600160a01b0316813563530ca43760e11b1415602857808252602082f35b3682"
    "833781823684845af490503d82833e806041573d82fd5b503d81f3fea2646970667358"
    "22122015938e3bf2c49f5df5c1b7f9569fa85cc5d6f3074bb258a2dc0c7e299bc9e336"
    "64736f6c63430008040033"
).replace("\n", "")


# -------------------------------------------------------------- low-level helpers


def _hex_to_bytes(hex_str: str) -> bytes:
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    return bytes.fromhex(s)


def _encode_address(addr: str) -> bytes:
    """ABI-encode a single address as a 32-byte left-padded word."""
    raw = _hex_to_bytes(addr)
    if len(raw) != 20:
        raise ValueError(f"bad address: {addr}")
    return b"\x00" * 12 + raw


def to_checksum_address(addr: str) -> str:
    """EIP-55 checksum case for a 20-byte address."""
    lower = addr.lower().removeprefix("0x")
    if len(lower) != 40:
        raise ValueError(f"bad address: {addr}")
    h = keccak(lower.encode())
    out = ["0x"]
    for i, c in enumerate(lower):
        if c.isdigit():
            out.append(c)
        else:
            nibble = (h[i // 2] >> (4 if i % 2 == 0 else 0)) & 0xF
            out.append(c.upper() if nibble >= 8 else c)
    return "".join(out)


def _create2(factory: str, salt: bytes, init_code: bytes) -> str:
    init_hash = keccak(init_code)
    raw = b"\xff" + _hex_to_bytes(factory) + salt + init_hash
    h = keccak(raw)
    return to_checksum_address("0x" + h[12:].hex())


# -------------------------------------------------------------- Safe path


def safe_proxy_of(eoa: str) -> str:
    """Compute the Polymarket Safe proxy address for a given EOA."""
    salt = keccak(_encode_address(eoa))
    init_code = _hex_to_bytes(PM_SAFE_PROXY_CREATION_CODE_HEX) + _encode_address(
        PM_SAFE_MASTER_COPY
    )
    return _create2(PM_SAFE_FACTORY, salt, init_code)


# -------------------------------------------------------------- Magic path

_MAGIC_PREFIX = bytes.fromhex("3d3d606380380380913d393d73")  # 13B
_MAGIC_MID = bytes.fromhex(
    "5af4602a57600080fd5b602d8060366000396000f3363d3d373d3d3d363d73"
)  # 31B
_MAGIC_SUFFIX = bytes.fromhex("5af43d82803e903d91602b57fd5bf3")  # 15B

# cloneConstructor(bytes) calldata for empty bytes argument: selector || offset(0x20) || length(0)
_CLONE_CONSTRUCTOR_CALLDATA = (
    keccak(b"cloneConstructor(bytes)")[:4]
    + (32).to_bytes(32, "big")
    + (0).to_bytes(32, "big")
)


def magic_proxy_of(eoa: str) -> str:
    """Compute the Polymarket Magic-link proxy address for a given EOA."""
    salt = keccak(_hex_to_bytes(eoa))
    init_code = (
        _MAGIC_PREFIX
        + _hex_to_bytes(PM_MAGIC_FACTORY)
        + _MAGIC_MID
        + _hex_to_bytes(PM_MAGIC_IMPL)
        + _MAGIC_SUFFIX
        + _CLONE_CONSTRUCTOR_CALLDATA
    )
    return _create2(PM_MAGIC_FACTORY, salt, init_code)


# -------------------------------------------------------------- detection

ProxyKind = Literal["safe", "magic", "none"]


@dataclass
class ProxyCandidate:
    kind: Literal["safe", "magic"]
    address: str
    deployed: bool


@dataclass
class ProxyDetectionResult:
    kind: ProxyKind
    proxy: str | None
    candidates: list[ProxyCandidate]


async def _address_has_code(rpc_url: str, address: str) -> bool:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getCode",
        "params": [address.lower(), "latest"],
    }
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "rpc error"))
    result = body.get("result")
    return isinstance(result, str) and result != "0x"


# -------------------------------------------------------------- V2 DepositWallet path

# `predictWalletAddress(address impl, bytes32 salt)` — Solady-style
# DepositWalletFactory view. Used to derive the deterministic V2 deposit
# wallet address for a given owner EOA. salt = bytes32(owner_eoa).
_PREDICT_WALLET_SELECTOR = keccak(b"predictWalletAddress(address,bytes32)")[:4]


def _eoa_to_salt(eoa: str) -> bytes:
    """salt = bytes32 of the EOA — left-padded to 32 bytes per Solady convention."""
    raw = _hex_to_bytes(eoa)
    if len(raw) != 20:
        raise ValueError(f"bad eoa: {eoa}")
    return b"\x00" * 12 + raw


async def derive_v2_deposit_wallet(
    eoa: str,
    rpc_url: str,
    *,
    factory_address: str,
    impl_address: str,
) -> tuple[str, bool]:
    """Compute (and confirm) the V2 PM DepositWallet for `eoa`.

    Returns `(predicted_address, deployed)`. `deployed=True` iff the address
    has bytecode on-chain (i.e. owner has activated their V2 wallet on PM).

    On a mismatch with PM's actual deployment we surface that as the predicted
    address being non-empty + `deployed=False` so the frontend can prompt the
    user to "activate your PM V2 account by depositing $1".
    """
    selector = _PREDICT_WALLET_SELECTOR
    impl_word = _encode_address(impl_address)
    salt_word = _eoa_to_salt(eoa)
    call_data = "0x" + (selector + impl_word + salt_word).hex()

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {"to": factory_address.lower(), "data": call_data},
            "latest",
        ],
    }
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "rpc error"))
    result = body.get("result")
    if not isinstance(result, str) or len(result) != 66:  # 0x + 64 hex
        raise RuntimeError(f"unexpected predictWalletAddress return: {result!r}")
    # Last 20 bytes of the 32-byte word.
    addr = to_checksum_address("0x" + result[-40:])
    deployed = await _address_has_code(rpc_url, addr)
    return addr, deployed


async def detect_proxy(eoa: str, rpc_url: str) -> ProxyDetectionResult:
    """Probe both Safe and Magic candidates on-chain; whichever has bytecode
    is the user's active Polymarket proxy."""
    safe_addr = safe_proxy_of(eoa)
    magic_addr = magic_proxy_of(eoa)

    try:
        safe_deployed = await _address_has_code(rpc_url, safe_addr)
    except Exception as exc:
        logger.warning("safe proxy probe failed for %s: %s", eoa, exc)
        safe_deployed = False
    try:
        magic_deployed = await _address_has_code(rpc_url, magic_addr)
    except Exception as exc:
        logger.warning("magic proxy probe failed for %s: %s", eoa, exc)
        magic_deployed = False

    candidates = [
        ProxyCandidate(kind="safe", address=safe_addr, deployed=safe_deployed),
        ProxyCandidate(kind="magic", address=magic_addr, deployed=magic_deployed),
    ]

    if safe_deployed:
        return ProxyDetectionResult(kind="safe", proxy=safe_addr, candidates=candidates)
    if magic_deployed:
        return ProxyDetectionResult(kind="magic", proxy=magic_addr, candidates=candidates)
    return ProxyDetectionResult(kind="none", proxy=None, candidates=candidates)
