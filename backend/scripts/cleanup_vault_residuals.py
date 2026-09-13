"""Vault residual cleanup — admin one-shot ops.

The V4 vault occasionally accumulates "residual" tokens that aren't its
underlying (USDC.e):
  - Native USDC (someone sent it directly to the vault address)
  - pUSD (V3/V4 liquidation step-4 leftovers when PM unwrap didn't fully clear)

These don't break the contract — only USDC.e is accounted for in
`lpPoolBalance` — but they're stuck capital. This script pauses the vault,
sweeps non-underlying tokens to the admin EOA, unpauses; and (separately)
lets the admin re-deposit fresh USDC.e to the LP pool after swapping
those tokens externally on Uniswap / via PM Offramp.

Subcommands:
    status            Print vault token balances + LP pool + paused flag
    drain             Pause → emergencyWithdrawERC20(NATIVE_USDC, pUSD) → unpause
    deposit-lp        Approve USDC.e + depositLP(amount) — pushes admin EOA's
                      USDC.e back into the LP pool (run AFTER swapping the
                      drained tokens to USDC.e on Uniswap / via PM Offramp).

Pre-flight: `vault_admin_private_key` must be set in .env (no 0x prefix).
The admin address is verified against vault.admin() before any state-changing
tx; mismatch → abort.

Examples:
    cd backend && python -m scripts.cleanup_vault_residuals status
    cd backend && python -m scripts.cleanup_vault_residuals drain --yes
    cd backend && python -m scripts.cleanup_vault_residuals deposit-lp --amount 5.49 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal

from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

from app.config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)


# ---------- Token addresses (Polygon mainnet) ----------

NATIVE_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

# Tokens that the drain step pulls out of the vault. USDC.e is deliberately
# excluded — it's the underlying, drainage would silently break the LP
# accounting (`lpPoolBalance`).
DRAIN_TOKENS: list[tuple[str, str]] = [
    ("Native USDC", NATIVE_USDC),
    ("pUSD", PUSD),
]


# ---------- Mini ABIs ----------

ERC20_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "allowance",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

VAULT_ABI = [
    {
        "type": "function",
        "name": "admin",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "paused",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "lpPoolBalance",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "setPaused",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "p", "type": "bool"}],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "emergencyWithdrawERC20",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "depositLP",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "amount", "type": "uint256"}],
        "outputs": [],
    },
]


# ---------- helpers ----------


def _to_dec(raw: int) -> str:
    """6-decimal token base units → human string."""
    return f"{Decimal(raw) / Decimal(10**6):.6f}"


def _to_base(human: str) -> int:
    return int((Decimal(human) * Decimal(10**6)).to_integral_value())


def _ensure_admin_key() -> str:
    pk = (settings.vault_admin_private_key or "").strip()
    if not pk:
        print(
            "ERROR: vault_admin_private_key is empty. Set VAULT_ADMIN_PRIVATE_KEY "
            "in backend/.env (no 0x prefix) before running drain / deposit-lp.",
            file=sys.stderr,
        )
        sys.exit(2)
    if pk.startswith("0x"):
        pk = pk[2:]
    return pk


async def _make_w3(rpc_url: str) -> AsyncWeb3:
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    # Polygon emits non-standard `extraData`; web3 will reject blocks without
    # this middleware in some queries (mostly historical, but harmless in any
    # case).
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def _confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


async def _send_tx(w3, account, tx_built: dict, label: str) -> str:
    """Sign + send + wait. Receipt failure aborts the whole script."""
    signed = account.sign_transaction(tx_built)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = await w3.eth.send_raw_transaction(raw)
    tx_hex = tx_hash.hex() if isinstance(tx_hash, bytes) else str(tx_hash)
    if not tx_hex.startswith("0x"):
        tx_hex = "0x" + tx_hex
    logger.info("%s sent tx=%s — waiting for receipt …", label, tx_hex)
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hex, timeout=180)
    status = receipt.get("status") if isinstance(receipt, dict) else receipt.status
    if int(status or 0) != 1:
        print(f"ERROR: {label} reverted (tx={tx_hex})", file=sys.stderr)
        sys.exit(3)
    block = receipt.get("blockNumber") if isinstance(receipt, dict) else receipt.blockNumber
    logger.info("%s OK block=%s tx=%s", label, int(block), tx_hex)
    return tx_hex


async def _build_overrides(w3, sender: str, chain_id: int) -> dict:
    """EIP-1559 fee envelope mirroring vault_client._tx_overrides — base*2 + tip
    so Polygon's reorgs don't drop us; tip from settings."""
    nonce = await w3.eth.get_transaction_count(sender)
    latest = await w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or 0
    tip = settings.keeper_priority_fee_gwei * 10**9
    return {
        "from": sender,
        "nonce": nonce,
        "chainId": chain_id,
        "maxPriorityFeePerGas": tip,
        "maxFeePerGas": base_fee * 2 + tip,
    }


# ---------- subcommands ----------


async def cmd_status(w3, vault_address: str) -> None:
    vault = w3.eth.contract(address=w3.to_checksum_address(vault_address), abi=VAULT_ABI)
    admin = await vault.functions.admin().call()
    paused = await vault.functions.paused().call()
    lp_pool = await vault.functions.lpPoolBalance().call()

    print(f"Vault:        {vault_address}")
    print(f"  admin       = {admin}")
    print(f"  paused      = {paused}")
    print(f"  lpPoolBalance = {_to_dec(lp_pool)} USDC.e")

    print("\nVault token balances:")
    for label, token_addr in [
        ("Native USDC", NATIVE_USDC),
        ("USDC.e",      USDC_E),
        ("pUSD",        PUSD),
    ]:
        token = w3.eth.contract(
            address=w3.to_checksum_address(token_addr), abi=ERC20_ABI
        )
        bal = await token.functions.balanceOf(
            w3.to_checksum_address(vault_address)
        ).call()
        underlying = " (underlying)" if token_addr == USDC_E else ""
        print(f"  {label:11s} = {_to_dec(bal)}{underlying}")

    free_usdce = (
        await w3.eth.contract(
            address=w3.to_checksum_address(USDC_E), abi=ERC20_ABI
        ).functions.balanceOf(w3.to_checksum_address(vault_address)).call()
    ) - lp_pool
    print(f"\nUSDC.e free (= balanceOf − lpPoolBalance) = {_to_dec(free_usdce)}")


async def cmd_drain(w3, vault_address: str, auto_yes: bool) -> None:
    pk = _ensure_admin_key()
    account = Account.from_key(pk)
    vault = w3.eth.contract(address=w3.to_checksum_address(vault_address), abi=VAULT_ABI)

    onchain_admin = await vault.functions.admin().call()
    if onchain_admin.lower() != account.address.lower():
        print(
            f"ERROR: signing key resolves to {account.address} but vault.admin() "
            f"is {onchain_admin}. Refusing to proceed.",
            file=sys.stderr,
        )
        sys.exit(4)
    logger.info("admin EOA verified: %s", account.address)

    # Snapshot pre-state.
    paused_before = await vault.functions.paused().call()
    drain_plan: list[tuple[str, str, int]] = []  # (label, address, raw amount)
    for label, token_addr in DRAIN_TOKENS:
        token = w3.eth.contract(
            address=w3.to_checksum_address(token_addr), abi=ERC20_ABI
        )
        bal = await token.functions.balanceOf(
            w3.to_checksum_address(vault_address)
        ).call()
        if bal > 0:
            drain_plan.append((label, token_addr, int(bal)))

    if not drain_plan:
        print("Nothing to drain — vault holds 0 of the residual tokens.")
        return

    print(f"\nVault paused before: {paused_before}")
    print("Drain plan (from vault → admin EOA):")
    for label, addr, amt in drain_plan:
        print(f"  {label:11s} {_to_dec(amt):>15s}  ({addr})")
    print(f"\nAdmin EOA (recipient): {account.address}")
    if not _confirm(
        "Proceed: setPaused(true) → emergencyWithdrawERC20(...) → setPaused(false)?",
        auto_yes,
    ):
        print("Aborted.")
        return

    chain_id = int(settings.lending_chain_id)

    # Step 1: pause if not already paused.
    if not paused_before:
        overrides = await _build_overrides(w3, account.address, chain_id)
        tx = await vault.functions.setPaused(True).build_transaction(overrides)
        await _send_tx(w3, account, tx, "setPaused(true)")
    else:
        logger.info("vault already paused — skipping setPaused(true)")

    # Step 2: emergencyWithdrawERC20 for each residual token.
    for label, token_addr, amount in drain_plan:
        overrides = await _build_overrides(w3, account.address, chain_id)
        tx = await vault.functions.emergencyWithdrawERC20(
            w3.to_checksum_address(token_addr),
            account.address,
            amount,
        ).build_transaction(overrides)
        await _send_tx(w3, account, tx, f"emergencyWithdrawERC20({label}, {_to_dec(amount)})")

    # Step 3: unpause back to whatever it was before. If the vault was already
    # paused on entry, leave it paused — that was an intentional admin state
    # we shouldn't override.
    if not paused_before:
        overrides = await _build_overrides(w3, account.address, chain_id)
        tx = await vault.functions.setPaused(False).build_transaction(overrides)
        await _send_tx(w3, account, tx, "setPaused(false)")
    else:
        logger.warning(
            "vault was paused before drain; leaving paused — admin must "
            "setPaused(false) manually when ready"
        )

    print("\nDrain complete. Next steps:")
    print(
        "  1. swap drained tokens to USDC.e (Uniswap v3 for native USDC; "
        "DEX or PM Offramp for pUSD)"
    )
    print(
        "  2. run `python -m scripts.cleanup_vault_residuals deposit-lp "
        "--amount X.YY` to push USDC.e back into the LP pool"
    )


async def cmd_deposit_lp(w3, vault_address: str, amount_human: str, auto_yes: bool) -> None:
    pk = _ensure_admin_key()
    account = Account.from_key(pk)
    vault = w3.eth.contract(address=w3.to_checksum_address(vault_address), abi=VAULT_ABI)
    usdce = w3.eth.contract(
        address=w3.to_checksum_address(USDC_E), abi=ERC20_ABI
    )
    amount_raw = _to_base(amount_human)
    if amount_raw <= 0:
        print("ERROR: amount must be > 0", file=sys.stderr)
        sys.exit(2)

    bal = await usdce.functions.balanceOf(account.address).call()
    if bal < amount_raw:
        print(
            f"ERROR: admin EOA has only {_to_dec(bal)} USDC.e, can't deposit "
            f"{amount_human}",
            file=sys.stderr,
        )
        sys.exit(5)

    print(f"Admin EOA:   {account.address}")
    print(f"USDC.e bal:  {_to_dec(bal)}")
    print(f"Deposit:     {amount_human} USDC.e → vault {vault_address}")
    if not _confirm("Proceed: approve(vault, amount) → vault.depositLP(amount)?", auto_yes):
        print("Aborted.")
        return

    chain_id = int(settings.lending_chain_id)

    # Step 1: approve only if existing allowance is short — saves an
    # unnecessary tx when re-running after a partial run.
    current_allowance = await usdce.functions.allowance(
        account.address, w3.to_checksum_address(vault_address)
    ).call()
    if int(current_allowance) < amount_raw:
        overrides = await _build_overrides(w3, account.address, chain_id)
        tx = await usdce.functions.approve(
            w3.to_checksum_address(vault_address), amount_raw
        ).build_transaction(overrides)
        await _send_tx(w3, account, tx, f"USDC.e.approve(vault, {amount_human})")
    else:
        logger.info(
            "existing allowance %s ≥ deposit — skipping approve",
            _to_dec(int(current_allowance)),
        )

    # Step 2: depositLP.
    overrides = await _build_overrides(w3, account.address, chain_id)
    tx = await vault.functions.depositLP(amount_raw).build_transaction(overrides)
    await _send_tx(w3, account, tx, f"vault.depositLP({amount_human})")

    new_lp = await vault.functions.lpPoolBalance().call()
    print(f"\nDeposit complete. New lpPoolBalance = {_to_dec(new_lp)} USDC.e")


# ---------- entrypoint ----------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cleanup_vault_residuals")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print vault state + token balances")

    drain = sub.add_parser("drain", help="pause + emergencyWithdrawERC20 + unpause")
    drain.add_argument("--yes", action="store_true", help="skip confirmation prompt")

    dep = sub.add_parser("deposit-lp", help="approve + vault.depositLP(amount)")
    dep.add_argument("--amount", required=True, help="USDC.e to deposit (e.g. 5.49)")
    dep.add_argument("--yes", action="store_true", help="skip confirmation prompt")

    return p.parse_args(argv)


async def _main(argv: list[str]) -> None:
    args = _parse_args(argv)
    vault_address = (settings.kpax_vault_address or "").strip()
    if not vault_address:
        print("ERROR: kpax_vault_address not configured", file=sys.stderr)
        sys.exit(2)
    rpc_url = settings.polygon_rpc_url
    w3 = await _make_w3(rpc_url)

    if args.cmd == "status":
        await cmd_status(w3, vault_address)
    elif args.cmd == "drain":
        await cmd_drain(w3, vault_address, auto_yes=args.yes)
    elif args.cmd == "deposit-lp":
        await cmd_deposit_lp(w3, vault_address, args.amount, auto_yes=args.yes)


def main() -> None:
    asyncio.run(_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
