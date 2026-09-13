"""Diagnose the CLOB 400 'order signer must = API KEY' error end-to-end.

Steps:
  1. Auto-discover (or accept --token-id) a CTF token_id with positive
     keeper-proxy balance.
  2. Use the SDK to build a real sell order (no posting). Log:
       maker / signer / signatureType / order hash / signature
  3. Manually call `isValidSignature(orderHash, signature)` on the keeper
     proxy via web3 eth_call. If it returns 0x1626ba7e (ERC-1271 magic),
     EIP-1271 works -> CLOB 400 is *not* from EIP-1271.
  4. If --post: actually POST the signed order and capture the verbatim
     response (status + body). Default off to avoid moving funds.

Usage:
    cd backend && .venv/bin/python -m scripts.diagnose_clob_400 \
        [--token-id <decimal-or-0x-token-id>] \
        [--shares 1] \
        [--post]
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import httpx
from eth_account import Account
from web3 import HTTPProvider, Web3
from web3.middleware import ExtraDataToPOAMiddleware

from app.config import settings
# Side-effect import: applies the V2 SDK monkey-patch (POLY_1271 signer=maker).
from app.services.lending import polymarket_seller as _ps  # noqa: F401


CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
ERC1271_MAGIC = "0x1626ba7e"


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _w3() -> Web3:
    w3 = Web3(HTTPProvider(settings.polygon_rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def auto_find_token_id(proxy: str) -> Optional[str]:
    """Pull keeper proxy positions from PM's data API. Returns the first
    token id whose balance > 0, or None."""
    url = f"https://data-api.polymarket.com/positions?user={proxy.lower()}"
    try:
        r = httpx.get(url, timeout=12)
        r.raise_for_status()
        items = r.json()
    except Exception as exc:
        print(f"    [WARN] data-api positions fetch failed: {exc}")
        return None
    if not isinstance(items, list):
        return None
    for it in items:
        size = it.get("size") or it.get("balance")
        token_id = it.get("asset") or it.get("tokenId")
        if not token_id:
            continue
        try:
            if float(size) > 0:
                return str(token_id)
        except (TypeError, ValueError):
            continue
    return None


def ctf_balance(w3: Web3, owner: str, token_id: str) -> int:
    """ERC1155 balanceOf. token_id can be 0x-prefixed hex or decimal string."""
    token_id_int = int(token_id, 16) if token_id.startswith("0x") else int(token_id)
    abi = [{
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    }]
    c = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=abi)
    return c.functions.balanceOf(Web3.to_checksum_address(owner), token_id_int).call()


def proxy_code_summary(w3: Web3, addr: str) -> str:
    code = w3.eth.get_code(Web3.to_checksum_address(addr)).hex()
    if not code or code == "0x":
        return "NOT DEPLOYED"
    if code.startswith("0xef0100"):
        delegate = "0x" + code[8:48]
        return f"EIP-7702 delegated -> {delegate}"
    return f"contract bytecode ({len(code)} hex chars)"


def call_is_valid_signature(
    w3: Web3, contract_addr: str, hash_hex: str, sig_hex: str
) -> str:
    abi = [{
        "type": "function",
        "name": "isValidSignature",
        "stateMutability": "view",
        "inputs": [
            {"name": "_hash", "type": "bytes32"},
            {"name": "_signature", "type": "bytes"},
        ],
        "outputs": [{"name": "", "type": "bytes4"}],
    }]
    c = w3.eth.contract(address=Web3.to_checksum_address(contract_addr), abi=abi)
    try:
        result = c.functions.isValidSignature(
            bytes.fromhex(hash_hex.replace("0x", "")),
            bytes.fromhex(sig_hex.replace("0x", "")),
        ).call()
        return "0x" + result.hex()
    except Exception as exc:
        return f"REVERT/ERROR: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-id", default=None,
                        help="CTF token id (decimal or 0x-hex). Auto-discovered if omitted.")
    parser.add_argument("--shares", type=float, default=1.0,
                        help="Order size (CTF shares). Default 1.")
    parser.add_argument("--order-type", choices=["FOK", "FAK", "GTC"], default="FOK",
                        help="CLOB order type. PM UI uses FAK; default keeper uses FOK.")
    parser.add_argument("--checksum-addrs", action="store_true",
                        help="Encode maker/signer in EIP-55 checksum case (PM UI does this).")
    parser.add_argument("--ui-headers", action="store_true",
                        help="Add Origin + Referer headers like the PM web UI sends.")
    parser.add_argument("--post", action="store_true",
                        help="Actually POST the signed order to CLOB. Default off.")
    args = parser.parse_args()

    if args.ui_headers:
        from py_clob_client_v2.http_helpers import helpers as _http_helpers
        _orig_overload = _http_helpers._overload_headers

        def _overload_with_ui(method, headers):
            h = _orig_overload(method, headers)
            h["Origin"] = "https://polymarket.com"
            h["Referer"] = "https://polymarket.com/"
            h["User-Agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            )
            return h

        _http_helpers._overload_headers = _overload_with_ui
        print("(injected Origin/Referer headers for this run)")

    pk = settings.keeper_private_key
    if not pk:
        print("[FAIL] KEEPER_PRIVATE_KEY empty"); sys.exit(1)
    proxy = settings.keeper_proxy_address
    if not proxy:
        print("[FAIL] KEEPER_PROXY_ADDRESS empty"); sys.exit(1)
    keeper_eoa = Account.from_key(pk).address

    _hr("[1] Identities")
    print(f"keeper EOA   : {keeper_eoa}")
    print(f"keeper proxy : {Web3.to_checksum_address(proxy)}")
    w3 = _w3()
    print(f"proxy code   : {proxy_code_summary(w3, proxy)}")

    _hr("[2] Token id selection")
    token_id = args.token_id or auto_find_token_id(proxy)
    if not token_id:
        print("[FAIL] no token id and auto-discover came up empty.")
        sys.exit(1)
    print(f"token id     : {token_id}")
    bal = ctf_balance(w3, proxy, token_id)
    print(f"proxy CTF bal: {bal}")
    if bal <= 0:
        print("[FAIL] keeper proxy has 0 balance for that token. Aborting.")
        sys.exit(1)
    if int(args.shares) > bal:
        print(f"[WARN] requested shares {args.shares} > balance {bal}. Clamping to balance.")
        args.shares = float(bal)

    _hr("[3] CLOB SDK setup (POLY_1271 + funder=proxy)")
    from py_clob_client_v2 import (
        ApiCreds, ClobClient, MarketOrderArgs, OrderType, Side, SignatureTypeV2,
    )
    creds = ApiCreds(
        api_key=settings.polymarket_api_key,
        api_secret=settings.polymarket_api_secret,
        api_passphrase=settings.polymarket_api_passphrase,
    )
    funder_for_sdk = (
        Web3.to_checksum_address(proxy) if args.checksum_addrs else proxy
    )
    client = ClobClient(
        host=settings.polymarket_clob_url,
        chain_id=settings.lending_chain_id,
        key=pk,
        signature_type=SignatureTypeV2.POLY_1271,
        funder=funder_for_sdk,
        creds=creds,
    )
    api_keys = client.get_api_keys()
    print(f"creds OK, get_api_keys -> {api_keys}")

    _hr("[4] Build signed sell order (no post)")
    order_type = getattr(OrderType, args.order_type)
    print(f"order type   : {args.order_type}")
    market_price = client.calculate_market_price(
        str(int(token_id, 16) if token_id.startswith("0x") else int(token_id)),
        Side.SELL, float(args.shares), order_type,
    )
    print(f"calculate_market_price: {market_price}")
    signed = client.create_market_order(MarketOrderArgs(
        token_id=str(int(token_id, 16) if token_id.startswith("0x") else int(token_id)),
        amount=float(args.shares),
        side=Side.SELL,
        price=market_price,
        order_type=order_type,
    ))
    # Note: address case in the EIP-712 hash uses uint160 encoding (case-
    # insensitive). With --checksum-addrs we feed the funder in checksum
    # form so the body sent to PM matches the PM-UI casing — useful only
    # if PM's server validates strings rather than uint values.
    print(f"signed order:")
    print(f"  salt          : {signed.salt}")
    print(f"  maker         : {signed.maker}")
    print(f"  signer        : {signed.signer}")
    print(f"  tokenId       : {signed.tokenId}")
    print(f"  makerAmount   : {signed.makerAmount}")
    print(f"  takerAmount   : {signed.takerAmount}")
    print(f"  side          : {signed.side}")
    print(f"  signatureType : {signed.signatureType} ({SignatureTypeV2(int(signed.signatureType)).name})")
    print(f"  signature     : {signed.signature}")

    _hr("[5] Manual EIP-1271 validation against keeper proxy")
    from py_clob_client_v2.order_utils.exchange_order_builder_v2 import (
        ExchangeOrderBuilderV2,
    )
    from py_clob_client_v2.order_utils.model.order_data_v2 import OrderV2
    from py_clob_client_v2.config import get_contract_config

    cfg = get_contract_config(settings.lending_chain_id)
    try:
        is_neg_risk = client.get_neg_risk(signed.tokenId)
    except Exception as exc:
        print(f"[WARN] get_neg_risk failed: {exc}; assuming non-neg-risk")
        is_neg_risk = False
    exchange_addr = cfg.neg_risk_exchange_v2 if is_neg_risk else cfg.exchange_v2
    print(f"exchange     : {exchange_addr}  (neg_risk={is_neg_risk})")

    builder = ExchangeOrderBuilderV2(exchange_addr, settings.lending_chain_id, client.signer)
    order_for_hash = OrderV2(
        salt=signed.salt,
        maker=signed.maker,
        signer=signed.signer,
        tokenId=signed.tokenId,
        makerAmount=signed.makerAmount,
        takerAmount=signed.takerAmount,
        side=signed.side,
        signatureType=signed.signatureType,
        timestamp=signed.timestamp,
        metadata=signed.metadata,
        builder=signed.builder,
        expiration=signed.expiration,
    )
    typed = builder.build_order_typed_data(order_for_hash)
    order_hash = builder.build_order_hash(typed)
    print(f"order hash   : {order_hash}")
    verdict = call_is_valid_signature(w3, signed.maker, order_hash, signed.signature)
    print(f"isValidSignature(hash, sig) on maker -> {verdict}")
    if verdict == ERC1271_MAGIC:
        print("    -> EIP-1271 works. PM 400 must be from a non-1271 check (maker authorization, etc).")
    else:
        print("    -> EIP-1271 FAILED. POLY_1271 mode incompatible with this maker. Try a different signature type.")

    if not args.post:
        _hr("[6] (skipping POST — pass --post to actually submit)")
        return

    _hr("[6] POST signed order to CLOB")
    try:
        resp = client.post_order(signed, order_type=order_type)
        print(f"post_order returned: {resp}")
    except Exception as exc:
        # The SDK wraps HTTP errors; try to extract status + body from common patterns.
        body = getattr(exc, "response", None)
        if body is not None:
            try:
                print(f"HTTP {body.status_code}")
                print(f"body: {body.text}")
            except Exception:
                pass
        print(f"post_order raised: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
