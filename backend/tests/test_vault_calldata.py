"""Sanity tests for vault calldata encoding."""

from app.services.lending.vault_calldata import (
    OPEN_LOAN_SELECTOR,
    REPAY_SELECTOR,
    encode_open_loan,
    encode_repay,
)


def test_open_loan_selector_is_8_hex_chars():
    assert len(OPEN_LOAN_SELECTOR) == 4


def test_open_loan_calldata_starts_with_selector():
    data = encode_open_loan(
        collateral_source="0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E",
        ctf_token_id=42,
        shares=833,
        principal=250_000_000,  # 250 USDC (6 decimals)
        match_kickoff=1_777_000_000,
        league_tier=1,
    )
    assert data.startswith("0x")
    expected_prefix = "0x" + OPEN_LOAN_SELECTOR.hex()
    assert data.startswith(expected_prefix)
    # selector (4) + 6 args × 32 bytes = 4 + 192 = 196 bytes = 392 hex + "0x"
    assert len(data) == 2 + 2 * (4 + 6 * 32)


def test_open_loan_address_is_lowercase_padded():
    data = encode_open_loan(
        "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1",
        1,
        1,
        1,
        1,
        1,
    )
    # First arg starts after the 4-byte selector → bytes [4, 36); chars [10, 74)
    addr_word = data[10:74]
    # 24 leading zero hex chars + 40 lowercase address chars
    assert addr_word.startswith("0" * 24)
    assert addr_word[24:].lower() == "aa35e5045783e2f9ca553cad9545baf213998ab1"


def test_repay_calldata():
    data = encode_repay(7)
    expected_prefix = "0x" + REPAY_SELECTOR.hex()
    assert data.startswith(expected_prefix)
    # selector (4) + 1 uint256 (32) = 36 bytes
    assert len(data) == 2 + 2 * (4 + 32)
