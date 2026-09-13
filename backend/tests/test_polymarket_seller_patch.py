"""Verifies the SDK monkey-patch in polymarket_seller.

The patch makes POLY_1271 orders use ``signer = maker`` (proxy) instead of
the SDK default ``signer = EOA``. PM CLOB V2 rejects the EOA-signer variant
with ``"the order signer address has to be the address of the API KEY"``
because PM's database binds the API key to the smart-contract wallet, not
the signing EOA.

We import the module to trigger ``_install_proxy_signer_patch()`` and then
exercise ``ExchangeOrderBuilderV2.build_order`` directly with both modes.
"""

from __future__ import annotations

# Importing the seller module triggers the monkey-patch at import time.
from app.services.lending import polymarket_seller  # noqa: F401
from py_clob_client_v2.order_utils.exchange_order_builder_v2 import (
    ExchangeOrderBuilderV2,
)
from py_clob_client_v2.order_utils.model.order_data_v2 import OrderDataV2, Side
from py_clob_client_v2.order_utils.model.signature_type_v2 import SignatureTypeV2
from py_clob_client_v2.signer import Signer


# Anvil-style well-known test key (account #0). Public address:
# 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266.
_TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_EOA = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_PROXY = "0xa996B5eE84209B7F73CE3573b71e8261547c431f"
_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"  # neg-risk V2


def _new_builder() -> ExchangeOrderBuilderV2:
    return ExchangeOrderBuilderV2(
        contract_address=_EXCHANGE,
        chain_id=137,
        signer=Signer(_TEST_PK, 137),
    )


def _data(sig_type: SignatureTypeV2, signer: str | None = None) -> OrderDataV2:
    return OrderDataV2(
        maker=_PROXY,
        tokenId="1",
        makerAmount="1000000",
        takerAmount="350000",
        side=Side.SELL,
        signer=signer or "",
        signatureType=sig_type,
        timestamp="0",
    )


def test_patch_applied_marker():
    """The patch sets a sentinel attribute and is idempotent."""
    assert getattr(ExchangeOrderBuilderV2, "_kpax_proxy_signer_patched", False)
    polymarket_seller._install_proxy_signer_patch()
    polymarket_seller._install_proxy_signer_patch()
    assert getattr(ExchangeOrderBuilderV2, "_kpax_proxy_signer_patched", False)


def test_poly_1271_forces_signer_to_maker():
    """POLY_1271 mode: signer is overwritten to maker even though the SDK's
    Signer holds a different EOA. No ValueError is raised."""
    builder = _new_builder()
    order = builder.build_order(_data(SignatureTypeV2.POLY_1271))
    assert order.maker == _PROXY
    assert order.signer == _PROXY  # patched: was EOA before
    assert int(order.signatureType) == int(SignatureTypeV2.POLY_1271)


def test_poly_1271_ignores_caller_signer_field():
    """Any signer the caller passed in is ignored for POLY_1271 — PM only
    accepts signer = maker."""
    builder = _new_builder()
    order = builder.build_order(
        _data(SignatureTypeV2.POLY_1271, signer=_EOA),
    )
    assert order.signer == _PROXY


def test_eoa_mode_still_requires_signer_eq_eoa():
    """Non-POLY_1271 paths preserve the original SDK guard: the order's
    signer must match the configured Signer's address (the EOA)."""
    builder = _new_builder()
    # Default signer falls back to maker (proxy), which != EOA → raises.
    try:
        builder.build_order(_data(SignatureTypeV2.EOA))
    except ValueError as exc:
        assert "signer does not match" in str(exc)
    else:
        raise AssertionError("expected ValueError for EOA mode with proxy maker")


def test_eoa_mode_accepts_eoa_signer():
    builder = _new_builder()
    order = builder.build_order(_data(SignatureTypeV2.EOA, signer=_EOA))
    assert order.signer == _EOA


def test_signed_order_uses_erc7739_wrapper(monkeypatch):
    """build_signed_order in POLY_1271 mode produces an ERC-7739 wrapped
    signature (not plain 65-byte ECDSA), so PM CLOB V2 accepts it.

    Layout: ECDSA(65) || APP_DOMAIN_SEPARATOR(32) || contentsHash(32) ||
            contentsType(N) || uint16 contentsTypeLen(2)

    We mock ``fetch_wallet_domain`` to avoid an RPC call in the unit test.
    """
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    from app.services.lending import erc7739
    from app.services.lending.erc7739 import WalletDomain

    monkeypatch.setattr(
        erc7739,
        "fetch_wallet_domain",
        lambda rpc, addr: WalletDomain(
            name="DepositWallet",
            version="1",
            chain_id=137,
            verifying_contract=addr,
            fields=0x0f,
        ),
    )
    polymarket_seller._wallet_domain_cache.clear()

    builder = _new_builder()
    signed = builder.build_signed_order(_data(SignatureTypeV2.POLY_1271))
    sig_bytes = bytes.fromhex(signed.signature.replace("0x", ""))

    # Trailing 2 bytes = uint16 contentsType length.
    contents_type_len = int.from_bytes(sig_bytes[-2:], "big")
    assert contents_type_len > 0
    expected_total = 65 + 32 + 32 + contents_type_len + 2
    assert len(sig_bytes) == expected_total, (
        f"wrapped sig should be {expected_total} bytes, got {len(sig_bytes)}"
    )

    contents_type_bytes = sig_bytes[-(2 + contents_type_len) : -2]
    assert contents_type_bytes.startswith(b"Order(uint256 salt,address maker,")

    # The 32 bytes right before contents_type are the inner contents hash;
    # 32 bytes before that are the V2 Exchange APP_DOMAIN_SEPARATOR. Both
    # must match the SDK's typed-data computation for the same order.
    typed = builder.build_order_typed_data(signed)
    encoded = encode_typed_data(full_message=typed)
    contents_hash = sig_bytes[65 + 32 : 65 + 64]
    app_domain_sep = sig_bytes[65 : 65 + 32]
    assert contents_hash == encoded.body
    assert app_domain_sep == encoded.header

    # The ECDSA signs the parent TypedDataSign hash, anchored against the
    # **app domain separator** (not the wallet domain — see erc7739.py
    # docstring). Recovering against that hash must yield the signing EOA.
    type_hash = erc7739._typed_data_sign_typehash(
        "Order", polymarket_seller._CONTENTS_TYPE_V2_ORDER
    )
    sign_struct_hash = erc7739.keccak(
        type_hash
        + contents_hash
        + erc7739.keccak(b"DepositWallet")
        + erc7739.keccak(b"1")
        + (137).to_bytes(32, "big")
        + erc7739._addr_b32(signed.maker)
        + b"\x00" * 32
    )
    parent_hash = erc7739.keccak(
        b"\x19\x01" + app_domain_sep + sign_struct_hash
    )
    recovered = Account._recover_hash(parent_hash, signature=sig_bytes[:65])
    assert recovered.lower() == _EOA.lower()
