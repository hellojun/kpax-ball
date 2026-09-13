"""Diagnose Polymarket CLOB + Relayer auth without moving any funds.

Checks:
  1. keeper_private_key → derived EOA address
  2. CLOB API key: tries to read order book (no auth needed) then checks
     if API creds match the keeper EOA by calling get_api_keys()
  3. If no CLOB creds configured: auto-derives them and prints for .env
  4. Relayer: reads wallet nonce (no auth) then tries a dry-run submit
     to check if the API key is valid

Usage:
    cd backend && python -m scripts.check_pm_auth
"""

import sys
from eth_account import Account

from app.config import settings


def main():
    print("=" * 60)
    print("Polymarket Auth Diagnostic")
    print("=" * 60)

    # ---------- 1. Keeper EOA ----------
    pk = settings.keeper_private_key
    if not pk:
        print("\n[FAIL] KEEPER_PRIVATE_KEY is empty. Cannot proceed.")
        sys.exit(1)
    acct = Account.from_key(pk)
    print(f"\n[1] Keeper EOA: {acct.address}")

    proxy = settings.keeper_proxy_address
    print(f"    Keeper proxy (funder): {proxy or '(not set)'}")
    if not proxy:
        print("    [FAIL] KEEPER_PROXY_ADDRESS not set.")
        sys.exit(1)

    # ---------- 2. CLOB API creds ----------
    print(f"\n[2] CLOB API Key configured: {bool(settings.polymarket_api_key)}")
    if settings.polymarket_api_key:
        print(f"    API Key: {settings.polymarket_api_key[:8]}...")

    from py_clob_client_v2 import ClobClient, SignatureTypeV2

    # Build client with keeper key (no creds yet for test)
    client_no_creds = ClobClient(
        host=settings.polymarket_clob_url,
        chain_id=settings.lending_chain_id,
        key=pk,
        signature_type=SignatureTypeV2.POLY_1271,
        funder=proxy,
    )

    # Test: can we reach the CLOB at all?
    try:
        client_no_creds.get_server_time()
        print("    [OK] CLOB reachable (get_server_time)")
    except Exception as e:
        print(f"    [FAIL] CLOB unreachable: {e}")
        print("    (Possible geo-block or network issue)")
        sys.exit(1)

    # If creds are configured, verify they match the keeper EOA
    if settings.polymarket_api_key and settings.polymarket_api_secret and settings.polymarket_api_passphrase:
        from py_clob_client_v2 import ApiCreds
        creds = ApiCreds(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_api_secret,
            api_passphrase=settings.polymarket_api_passphrase,
        )
        client_with_creds = ClobClient(
            host=settings.polymarket_clob_url,
            chain_id=settings.lending_chain_id,
            key=pk,
            signature_type=SignatureTypeV2.POLY_1271,
            funder=proxy,
            creds=creds,
        )
        try:
            keys = client_with_creds.get_api_keys()
            print(f"    [OK] get_api_keys returned: {keys}")
        except Exception as e:
            print(f"    [FAIL] get_api_keys failed: {e}")
            print("    → The API key is probably bound to a different EOA.")
            print("    → Fix: clear POLYMARKET_API_KEY/SECRET/PASSPHRASE from .env,")
            print("      restart, and let the auto-derive path create new creds.")
    else:
        print("\n[3] No CLOB API creds configured. Attempting auto-derive...")
        try:
            derived = client_no_creds.create_or_derive_api_key()
            print(f"    [OK] Derived API creds for {acct.address}:")
            print(f"    POLYMARKET_API_KEY={derived.api_key}")
            print(f"    POLYMARKET_API_SECRET={derived.api_secret}")
            print(f"    POLYMARKET_API_PASSPHRASE={derived.api_passphrase}")
            print("\n    → Copy the 3 lines above into your .env")
        except Exception as e:
            print(f"    [FAIL] Auto-derive failed: {e}")

    # ---------- 3. Relayer ----------
    print(f"\n[4] Relayer URL: {settings.polymarket_relayer_url}")
    print(f"    Relayer API Key configured: {bool(settings.polymarket_relayer_api_key)}")
    if settings.polymarket_relayer_api_key:
        print(f"    Relayer API Key: {settings.polymarket_relayer_api_key[:8]}...")

    if not settings.polymarket_relayer_api_key:
        print("    [FAIL] POLYMARKET_RELAYER_API_KEY not set.")
        print("    → Get this from PM account settings (Relayer API section)")
        print("      while connected with the keeper EOA.")
    else:
        # Try to read the wallet nonce (on-chain, no relayer auth needed)
        from web3 import Web3, HTTPProvider
        from web3.middleware import ExtraDataToPOAMiddleware
        w3 = Web3(HTTPProvider(settings.polygon_rpc_url))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        wallet_abi = [{"type":"function","name":"nonce","stateMutability":"view",
                       "inputs":[],"outputs":[{"name":"","type":"uint256"}]}]
        wallet = w3.eth.contract(
            address=Web3.to_checksum_address(proxy), abi=wallet_abi
        )
        try:
            nonce = wallet.functions.nonce().call()
            print(f"    [OK] Wallet on-chain nonce: {nonce}")
        except Exception as e:
            print(f"    [WARN] Could not read wallet nonce: {e}")

        # Dry-run: try submitting an empty-ish request to see if auth passes
        # (will fail with a payload error, but auth should pass first)
        import httpx
        try:
            r = httpx.get(
                settings.polymarket_relayer_url.rstrip("/") + "/transaction",
                params={"id": "nonexistent-test-id"},
                headers={
                    "RELAYER_API_KEY": settings.polymarket_relayer_api_key,
                    "RELAYER_API_KEY_ADDRESS": acct.address,
                },
                timeout=10,
            )
            if r.status_code == 401:
                print(f"    [FAIL] Relayer auth failed (401): {r.text[:200]}")
                print("    → API key is invalid or bound to a different EOA.")
                print("    → Re-create the key in PM settings with the keeper EOA.")
            elif r.status_code in (200, 404, 400):
                print(f"    [OK] Relayer auth passed (status={r.status_code})")
            else:
                print(f"    [WARN] Relayer returned {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"    [FAIL] Relayer request failed: {e}")

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()
