"""One-shot: derive Polymarket CLOB API creds for the keeper EOA.

Run this from a machine whose IP is NOT on Polymarket's Cloudflare blocklist.
The result is **deterministic per private key**, so the creds you get here will
also work when the worker re-uses them later (even if the worker itself can't
reach PM directly — though for actual order submission you'll still need a
non-blocked IP).

Usage:
  KEEPER_PRIVATE_KEY=0x...  python scripts/derive_polymarket_creds.py

Then copy the printed env lines into backend/.env and restart the worker.

Dependencies: only py-clob-client-v2 (Polymarket's CLOB V2 SDK; the V1
package no longer works against production since the 2026-04-28 cutover).
Quick install if missing:
  pip install py-clob-client-v2
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    pk = os.environ.get("KEEPER_PRIVATE_KEY", "").strip()
    if not pk:
        print(
            "error: set KEEPER_PRIVATE_KEY in env (with or without 0x prefix)",
            file=sys.stderr,
        )
        return 2
    if pk.startswith("0x"):
        pk = pk[2:]

    try:
        from py_clob_client_v2 import ClobClient
    except ImportError:
        print(
            "error: py-clob-client-v2 not installed. "
            "Run: pip install py-clob-client-v2",
            file=sys.stderr,
        )
        return 2

    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=pk,
    )
    creds = client.create_or_derive_api_key()

    print("# === Polymarket API creds for keeper EOA ===")
    print(f"POLYMARKET_API_KEY={creds.api_key}")
    print(f"POLYMARKET_API_SECRET={creds.api_secret}")
    print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
    print("# Append these to backend/.env (or export them) and restart the worker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
