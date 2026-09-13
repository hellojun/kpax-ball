"""Synthetic LendingVault log builders. Shared by `test_event_indexer.py`
and `test_integration_e2e.py`. The leading underscore keeps pytest from
collecting this file as a test module."""

from __future__ import annotations

from eth_abi import encode as abi_encode

from app.services.lending.event_decoder import (
    LOAN_LIQUIDATED_TOPIC0,
    LOAN_OPENED_TOPIC0,
    LOAN_REPAID_TOPIC0,
)


def _hex_word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _addr_topic(addr_hex: str) -> str:
    """Pad a 20-byte address into a 32-byte topic."""
    a = addr_hex.lower().removeprefix("0x")
    return "0x" + a.rjust(64, "0")


def make_loan_opened_log(
    *,
    loan_id: int,
    tx_hash: str,
    log_index: int,
    block_number: int,
    borrower: str = "0xCa" + "fe" * 19,
    collateral_source: str = "0xC0" + "11" * 19,
    ctf_token_id: int = 12345,
    shares: int = 1_000,
    principal: int = 250_000_000,
    match_kickoff: int = 1_700_000_000,
    league_tier: int = 1,
) -> dict:
    data = abi_encode(
        ["uint256", "uint256", "uint256", "uint256", "uint8"],
        [ctf_token_id, shares, principal, match_kickoff, league_tier],
    )
    return {
        "topics": [
            LOAN_OPENED_TOPIC0,
            _hex_word(loan_id),
            _addr_topic(borrower),
            _addr_topic(collateral_source),
        ],
        "data": "0x" + data.hex(),
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block_number),
        "address": "0x" + "ee" * 20,
    }


def make_loan_repaid_log(
    *,
    loan_id: int,
    tx_hash: str,
    log_index: int,
    block_number: int,
    principal_paid: int = 250_000_000,
    interest_paid: int = 1_000_000,
) -> dict:
    data = abi_encode(["uint256", "uint256"], [principal_paid, interest_paid])
    return {
        "topics": [LOAN_REPAID_TOPIC0, _hex_word(loan_id)],
        "data": "0x" + data.hex(),
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block_number),
        "address": "0x" + "ee" * 20,
    }


def make_loan_liquidated_log(
    *,
    loan_id: int,
    tx_hash: str,
    log_index: int,
    block_number: int,
    reason: str = "ltv_breach",
    actual_proceeds: int = 333_000_000,
    to_lp: int = 250_000_000,
    to_treasury: int = 30_000_000,
    residual: int = 53_000_000,
) -> dict:
    """V2 LoanLiquidated has 5 non-indexed fields:
    (string reason, uint256 actualProceeds, uint256 toLp, uint256 toTreasury,
    uint256 residualToBorrower)."""
    data = abi_encode(
        ["string", "uint256", "uint256", "uint256", "uint256"],
        [reason, actual_proceeds, to_lp, to_treasury, residual],
    )
    return {
        "topics": [LOAN_LIQUIDATED_TOPIC0, _hex_word(loan_id)],
        "data": "0x" + data.hex(),
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block_number),
        "address": "0x" + "ee" * 20,
    }
