"""Re-fire the PM Relayer pUSD transfer from keeper proxy to vault.

Used after step-4 of admin manual liquidation crashed with `relayer
/submit returned 401: invalid authorization`. Hits PM Relayer via the
same client the keeper uses, so we test the header fix in pm_relayer.py
in isolation (separate process, no backend restart required).

Usage:
    cd backend && .venv/bin/python -m scripts.recover_proxy_pusd
        [--amount-e6 <int>]  # default: full proxy pUSD balance

Reads:
  POLYMARKET_RELAYER_API_KEY, KEEPER_PRIVATE_KEY, KEEPER_PROXY_ADDRESS,
  POLYMARKET_RELAYER_URL, KPAX_VAULT_ADDRESS, POLYGON_RPC_URL, etc.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
from web3 import Web3

from app.config import settings
from app.services.lending.pm_relayer import PMRelayerClient


PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


async def proxy_pusd_balance() -> int:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{
            "to": PUSD,
            "data": "0x70a08231" + "0" * 24
                    + settings.keeper_proxy_address.lower().removeprefix("0x"),
        }, "latest"],
    }
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(settings.polygon_rpc_url, json=payload)
        return int(r.json()["result"], 16)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--amount-e6", type=int, default=None,
        help="pUSD amount in 6-decimal base units. Default: full proxy balance.",
    )
    args = p.parse_args()

    bal = await proxy_pusd_balance()
    print(f"keeper proxy pUSD balance: {bal} ({bal / 1e6} pUSD)")
    if bal == 0:
        print("nothing to recover. exiting.")
        return

    amount = args.amount_e6 or bal
    if amount > bal:
        print(f"FAIL: requested amount {amount} > balance {bal}")
        sys.exit(1)

    vault = Web3.to_checksum_address(settings.kpax_vault_address)
    print(
        f"transferring {amount} pUSD ({amount / 1e6}) from "
        f"{settings.keeper_proxy_address} -> {vault} via PM Relayer"
    )

    client = PMRelayerClient(
        rpc_url=settings.polygon_rpc_url,
        relayer_url=settings.polymarket_relayer_url,
        api_key=settings.polymarket_relayer_api_key,
        owner_private_key=settings.keeper_private_key,
        wallet_address=settings.keeper_proxy_address,
        factory_address=settings.polymarket_deposit_wallet_factory,
        chain_id=settings.lending_chain_id,
    )
    try:
        # PM Relayer /submit with RELAYER_API_KEY (gasless). The on-chain
        # `factory.proxy(...)` path needs operator role on the factory,
        # which keeper EOA doesn't have — so PM Relayer is the only path
        # that actually works.
        receipt = await client.transfer_erc20(PUSD, vault, amount)
        print(f"SUCCESS: tx_id={receipt.transaction_id} state={receipt.state} "
              f"hash={receipt.transaction_hash}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
