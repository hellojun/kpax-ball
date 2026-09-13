"""ERC-7739-style nested EIP-712 signature wrapping for PM V2 DepositWallet.

PM CLOB V2 verifies orders signed by their V2 DepositWallet (impl
``0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB``) via a Solady-style
TypedDataSign wrapper. Plain ECDSA over the order's hash is rejected with
``{"error": "invalid signature"}`` because the wallet's
``isValidSignature`` runs the recovery against a different parent hash.

Wrapper layout (concatenated bytes, recovered from a captured PM Web UI
sell):
    ecdsaSig             (65 bytes, r || s || v)
    APP_DOMAIN_SEPARATOR (32 bytes, the inner contents' EIP-712 domain
                                    — i.e. V2 CTFExchange's domain)
    contentsHash         (32 bytes, hashStruct of the inner Order)
    contentsType         (N bytes, ASCII type string
                                   "Order(uint256 salt,address maker,...)")
    contentsTypeLen      (2 bytes, uint16 big-endian)

What the ECDSA signs (verified by recovering against a captured sig +
keeper EOA on 2026-05-08):

    parentHash = keccak256(
        0x1901
        || APP_DOMAIN_SEPARATOR        // anchored to the *contents'* domain
        || keccak256(
            keccak256(typeString)
            || contentsHash
            || keccak256(walletName)
            || keccak256(walletVersion)
            || walletChainId           // uint256
            || walletVerifyingContract // address as uint160 padded
            || bytes32(0)              // salt (always 32 bytes, zero for fields=0x0f)
        )
    )

    typeString = "TypedDataSign(<ContentsName> contents,string name,"
                 "string version,uint256 chainId,address verifyingContract,"
                 "bytes32 salt)<contentsType>"

Notably this is an **earlier Solady ERC-1271 variant** that:
- Drops ``bytes1 fields`` and ``uint256[] extensions`` from both the
  type string and the struct hash (current Solady includes them).
- Anchors the parent hash with the **contents' app domain separator**
  (the V2 Exchange domain), not the wallet's own EIP-712 domain.

The wallet's own domain ("DepositWallet" / "1" / chainId / wallet addr)
shows up only as fields *inside* the TypedDataSign struct.

References:
  - https://eips.ethereum.org/EIPS/eip-7739 (newer variant — does NOT match
    PM V2 DepositWallet exactly)
  - The impl bytecode at 0x58CA…1eB embeds the literal substring
    `" contents,string name,string version,uint256 chainId,address verifyingContract,bytes32 salt)"`
    confirming the type-string template.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account
from eth_utils import keccak

def _addr_b32(addr: str) -> bytes:
    raw = addr.lower().removeprefix("0x")
    if len(raw) != 40:
        raise ValueError(f"bad address: {addr}")
    return b"\x00" * 12 + bytes.fromhex(raw)


def _u256(n: int) -> bytes:
    return n.to_bytes(32, "big")


@dataclass(frozen=True)
class WalletDomain:
    """The EIP-5267 domain reported by the smart-contract wallet's
    ``eip712Domain()``. For PM V2 DepositWallet it's name='DepositWallet',
    version='1', fields=0x0f."""

    name: str
    version: str
    chain_id: int
    verifying_contract: str
    fields: int = 0x0f


def _typed_data_sign_typehash(contents_name: str, contents_type: str) -> bytes:
    """Build the V2-DepositWallet TypedDataSign typehash. Drops both the
    ``bytes1 fields`` and ``uint256[] extensions`` placeholders that current
    Solady carries — the impl bytecode at 0x58CA…1eB only embeds the literal
    substring ``" contents,string name,string version,uint256 chainId,address
    verifyingContract,bytes32 salt)"``. Adding the extra fields silently
    breaks the recovery (we verified by recovering against a captured PM
    Web UI sig: only this exact template recovers the signer EOA)."""
    type_string = (
        b"TypedDataSign("
        + contents_name.encode()
        + b" contents,string name,string version,uint256 chainId,"
        + b"address verifyingContract,bytes32 salt)"
        + contents_type.encode()
    )
    return keccak(type_string)


def wrap_signature_erc7739(
    *,
    private_key: str,
    contents_hash: bytes,
    contents_name: str,
    contents_type: str,
    app_domain_separator: bytes,
    wallet_domain: WalletDomain,
) -> bytes:
    """Sign ``contents_hash`` (hashStruct of the inner Order) with the V2
    DepositWallet's TypedDataSign wrapper. Returns the bytes that PM CLOB V2
    accepts as ``order.signature`` for POLY_1271 orders.

    The parent EIP-712 anchor here is the **contents' app domain separator**
    (V2 CTFExchange's domain), not the wallet's own domain — this is the
    counterintuitive bit that makes this Solady variant differ from the
    final ERC-7739 spec.
    """
    if len(contents_hash) != 32:
        raise ValueError(f"contents_hash must be 32 bytes, got {len(contents_hash)}")
    if len(app_domain_separator) != 32:
        raise ValueError(
            f"app_domain_separator must be 32 bytes, got {len(app_domain_separator)}"
        )

    type_hash = _typed_data_sign_typehash(contents_name, contents_type)
    sign_struct_hash = keccak(
        type_hash
        + contents_hash
        + keccak(wallet_domain.name.encode())
        + keccak(wallet_domain.version.encode())
        + _u256(wallet_domain.chain_id)
        + _addr_b32(wallet_domain.verifying_contract)
        + b"\x00" * 32  # salt (always present in TypedDataSign struct)
    )
    parent_hash = keccak(b"\x19\x01" + app_domain_separator + sign_struct_hash)
    signed = Account._sign_hash(parent_hash, private_key=private_key)
    ecdsa_sig = bytes(signed.signature)
    contents_type_bytes = contents_type.encode()
    return (
        ecdsa_sig
        + app_domain_separator
        + contents_hash
        + contents_type_bytes
        + len(contents_type_bytes).to_bytes(2, "big")
    )


def fetch_wallet_domain(rpc_url: str, wallet_address: str) -> WalletDomain:
    """Read EIP-5267 `eip712Domain()` from `wallet_address`. Used at startup
    to discover the parent domain of whatever smart-contract wallet the
    keeper is using (V2 DepositWallet, Safe, EIP-7702 EOA, etc).
    """
    import httpx

    selector = keccak(b"eip712Domain()")[:4]
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {"to": wallet_address.lower(), "data": "0x" + selector.hex()},
            "latest",
        ],
    }
    resp = httpx.post(rpc_url, json=payload, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"eip712Domain() rpc error: {body['error']}")
    raw = bytes.fromhex(body["result"][2:])

    # ABI decode (bytes1, string, string, uint256, address, bytes32, uint256[]):
    # head: 7 * 32 bytes. fields(slot0), name_offset, version_offset, chainId,
    # verifying, salt, extensions_offset.
    fields = raw[0]                                       # bytes1, left-aligned
    name_off = int.from_bytes(raw[32:64], "big")
    version_off = int.from_bytes(raw[64:96], "big")
    chain_id = int.from_bytes(raw[96:128], "big")
    verifying = "0x" + raw[128 + 12 : 128 + 32].hex()

    def _read_string(off: int) -> str:
        length = int.from_bytes(raw[off : off + 32], "big")
        return raw[off + 32 : off + 32 + length].decode()

    name = _read_string(name_off)
    version = _read_string(version_off)
    return WalletDomain(
        name=name,
        version=version,
        chain_id=chain_id,
        verifying_contract=verifying,
        fields=fields,
    )
