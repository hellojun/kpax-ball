"""Compare SDK-computed EIP-712 hash vs on-chain V2 CTFExchange.hashOrder
result. If they differ, the SDK's typed-data spec disagrees with the
contract — that explains the PM "invalid signature" 400 even though the
local isValidSignature succeeds (we'd be passing PM a hash PM never
recomputes).

Usage: cd backend && .venv/bin/python -m scripts.verify_order_hash
"""

from __future__ import annotations

from eth_account import Account
from web3 import HTTPProvider, Web3
from web3.middleware import ExtraDataToPOAMiddleware

from app.config import settings
from app.services.lending import polymarket_seller as _ps  # noqa: F401  (apply patch)


def main() -> None:
    pk = settings.keeper_private_key
    proxy = settings.keeper_proxy_address
    keeper_eoa = Account.from_key(pk).address
    print(f"keeper EOA = {keeper_eoa}")
    print(f"keeper proxy = {proxy}\n")

    from py_clob_client_v2 import (
        ApiCreds, ClobClient, MarketOrderArgs, OrderType, Side, SignatureTypeV2,
    )
    creds = ApiCreds(
        api_key=settings.polymarket_api_key,
        api_secret=settings.polymarket_api_secret,
        api_passphrase=settings.polymarket_api_passphrase,
    )
    client = ClobClient(
        host=settings.polymarket_clob_url,
        chain_id=settings.lending_chain_id,
        key=pk,
        signature_type=SignatureTypeV2.POLY_1271,
        funder=proxy,
        creds=creds,
    )

    token_id = "106923577561832841610259011555602782394407982555388440772837857770573361593592"
    mp = client.calculate_market_price(token_id, Side.SELL, 1.0, OrderType.FOK)
    signed = client.create_market_order(MarketOrderArgs(
        token_id=token_id, amount=1.0, side=Side.SELL, price=mp, order_type=OrderType.FOK,
    ))
    print(f"signer (in order body) = {signed.signer}")
    print(f"maker  (in order body) = {signed.maker}\n")

    from py_clob_client_v2.order_utils.exchange_order_builder_v2 import (
        ExchangeOrderBuilderV2,
    )
    from py_clob_client_v2.config import get_contract_config

    cfg = get_contract_config(settings.lending_chain_id)
    is_neg = client.get_neg_risk(signed.tokenId)
    exchange_addr = cfg.neg_risk_exchange_v2 if is_neg else cfg.exchange_v2
    print(f"exchange = {exchange_addr} (neg_risk={is_neg})\n")

    builder = ExchangeOrderBuilderV2(exchange_addr, settings.lending_chain_id, client.signer)
    typed = builder.build_order_typed_data(signed)
    sdk_hash = builder.build_order_hash(typed)
    print(f"SDK computed hash      = {sdk_hash}")

    w3 = Web3(HTTPProvider(settings.polygon_rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    # hashOrder on V2 exchange. Order tuple matches the SDK struct order.
    abi = [{
        "type": "function",
        "name": "hashOrder",
        "stateMutability": "view",
        "inputs": [{
            "name": "order", "type": "tuple",
            "components": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
                {"name": "timestamp", "type": "uint256"},
                {"name": "metadata", "type": "bytes32"},
                {"name": "builder", "type": "bytes32"},
                {"name": "signature", "type": "bytes"},
            ],
        }],
        "outputs": [{"name": "", "type": "bytes32"}],
    }]
    c = w3.eth.contract(address=Web3.to_checksum_address(exchange_addr), abi=abi)
    order_tuple = (
        int(signed.salt),
        Web3.to_checksum_address(signed.maker),
        Web3.to_checksum_address(signed.signer),
        int(signed.tokenId),
        int(signed.makerAmount),
        int(signed.takerAmount),
        int(signed.side),
        int(signed.signatureType),
        int(signed.timestamp),
        bytes.fromhex(signed.metadata.replace("0x", "")),
        bytes.fromhex(signed.builder.replace("0x", "")),
        bytes.fromhex(signed.signature.replace("0x", "")),
    )
    onchain_hash = c.functions.hashOrder(order_tuple).call()
    onchain_hex = "0x" + onchain_hash.hex()
    print(f"on-chain hashOrder()   = {onchain_hex}")

    if sdk_hash.lower() == onchain_hex.lower():
        print("\n[OK] SDK hash matches on-chain hash. EIP-712 spec is correct.")
        print("     PM 'invalid signature' must be from a non-hash check.")
    else:
        print("\n[FAIL] SDK hash != on-chain hash.")
        print("       SDK's typed-data spec disagrees with the V2 contract.")
        print("       This explains why PM rejects: PM recomputes via the contract spec,")
        print("       gets a different hash, and our signature fails to verify.")


if __name__ == "__main__":
    main()
