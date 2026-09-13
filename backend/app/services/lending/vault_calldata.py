"""ABI-encoding helpers for LendingVault calldata. Used by /prepare-borrow
to hand the frontend the exact bytes it should pass to MetaMask.

The selectors are derived from the function signatures, so they stay correct
if Foundry (or anyone else) recompiles the contract — there's no hardcoded
4-byte literal to drift.
"""

from __future__ import annotations

from eth_abi import encode
from eth_hash.auto import keccak


def _selector(signature: str) -> bytes:
    """Compute the 4-byte function selector from a Solidity signature string."""
    return keccak(signature.encode())[:4]


# openLoan V2 (legacy 6-arg, kept for V2 vault deployments) → loanId(uint256)
_OPEN_LOAN_V2_SIG = "openLoan(address,uint256,uint256,uint256,uint256,uint8)"
OPEN_LOAN_V2_SELECTOR = _selector(_OPEN_LOAN_V2_SIG)
# Backwards-compat alias — pre-V3 callsites import this name directly.
OPEN_LOAN_SELECTOR = OPEN_LOAN_V2_SELECTOR

# openLoan V3 (7-arg, with borrowerProxy) — distinct selector from V2.
_OPEN_LOAN_V3_SIG = (
    "openLoan(address,uint256,uint256,uint256,uint256,uint8,address)"
)
OPEN_LOAN_V3_SELECTOR = _selector(_OPEN_LOAN_V3_SIG)


def encode_open_loan(
    collateral_source: str,
    ctf_token_id: int,
    shares: int,
    principal: int,
    match_kickoff: int,
    league_tier: int,
) -> str:
    """V2 calldata for `LendingVault.openLoan(...)`. Kept for legacy paths;
    new flows should use `encode_open_loan_v3`."""
    body = encode(
        ["address", "uint256", "uint256", "uint256", "uint256", "uint8"],
        [
            collateral_source,
            ctf_token_id,
            shares,
            principal,
            match_kickoff,
            league_tier,
        ],
    )
    return "0x" + (OPEN_LOAN_V2_SELECTOR + body).hex()


def encode_open_loan_v3(
    collateral_source: str,
    ctf_token_id: int,
    shares: int,
    principal: int,
    match_kickoff: int,
    league_tier: int,
    borrower_proxy: str,
) -> str:
    """V3 calldata for `LendingVaultV3.openLoan(...)` (7-arg).

    `borrower_proxy` is the borrower's PM V2 DepositWallet — destination for
    the wrapped pUSD AND the registered caller for `repay`. Predict it via
    `proxy_resolver.derive_v2_deposit_wallet`.
    """
    body = encode(
        ["address", "uint256", "uint256", "uint256", "uint256", "uint8", "address"],
        [
            collateral_source,
            ctf_token_id,
            shares,
            principal,
            match_kickoff,
            league_tier,
            borrower_proxy,
        ],
    )
    return "0x" + (OPEN_LOAN_V3_SELECTOR + body).hex()


# repay(uint256)
_REPAY_SIG = "repay(uint256)"
REPAY_SELECTOR = _selector(_REPAY_SIG)


def encode_repay(loan_id: int) -> str:
    body = encode(["uint256"], [loan_id])
    return "0x" + (REPAY_SELECTOR + body).hex()


# ERC20.approve(spender, amount) — used to build PM Relayer Batch calls so
# the borrower's DepositWallet pre-approves the vault to pull pUSD on repay.
_ERC20_APPROVE_SIG = "approve(address,uint256)"
ERC20_APPROVE_SELECTOR = _selector(_ERC20_APPROVE_SIG)


def encode_erc20_approve(spender: str, amount: int) -> str:
    body = encode(["address", "uint256"], [spender, amount])
    return "0x" + (ERC20_APPROVE_SELECTOR + body).hex()
