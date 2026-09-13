"""Decode LendingVault events from raw RPC logs (used by both
borrow/repay flows that poll a receipt, and the event indexer that
pulls logs in batches via `eth_getLogs`).

Indexed fields come from `topics`, non-indexed from the data blob.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from eth_abi import decode as abi_decode
from eth_hash.auto import keccak

logger = logging.getLogger(__name__)

# event LoanOpened(
#   uint256 indexed loanId,
#   address indexed borrower,
#   address indexed collateralSource,
#   uint256 ctfTokenId,
#   uint256 shares,
#   uint256 principal,
#   uint256 matchKickoff,
#   uint8 leagueTier
# )
_LOAN_OPENED_SIG = "LoanOpened(uint256,address,address,uint256,uint256,uint256,uint256,uint8)"
LOAN_OPENED_TOPIC0 = "0x" + keccak(_LOAN_OPENED_SIG.encode()).hex()

# event LoanRepaid(uint256 indexed loanId, uint256 principalPaid, uint256 interestPaid)
_LOAN_REPAID_SIG = "LoanRepaid(uint256,uint256,uint256)"
LOAN_REPAID_TOPIC0 = "0x" + keccak(_LOAN_REPAID_SIG.encode()).hex()

# V2 event signature (Sprint 4): adds toLp / toTreasury / residualToBorrower split.
# event LoanLiquidated(
#   uint256 indexed loanId, string reason,
#   uint256 actualProceeds, uint256 toLp, uint256 toTreasury, uint256 residualToBorrower
# )
_LOAN_LIQUIDATED_SIG = "LoanLiquidated(uint256,string,uint256,uint256,uint256,uint256)"
LOAN_LIQUIDATED_TOPIC0 = "0x" + keccak(_LOAN_LIQUIDATED_SIG.encode()).hex()

# event LiquidationStarted(uint256 indexed loanId, uint256 currentPriceE6, uint256 expectedProceeds)
_LIQUIDATION_STARTED_SIG = "LiquidationStarted(uint256,uint256,uint256)"
LIQUIDATION_STARTED_TOPIC0 = "0x" + keccak(_LIQUIDATION_STARTED_SIG.encode()).hex()

# event CtfReturnedFromKeeper(uint256 indexed loanId)
_CTF_RETURNED_SIG = "CtfReturnedFromKeeper(uint256)"
CTF_RETURNED_TOPIC0 = "0x" + keccak(_CTF_RETURNED_SIG.encode()).hex()

# event TreasurySet(address indexed previous, address indexed next)
_TREASURY_SET_SIG = "TreasurySet(address,address)"
TREASURY_SET_TOPIC0 = "0x" + keccak(_TREASURY_SET_SIG.encode()).hex()


@dataclass
class LoanOpenedEvent:
    loan_id: int
    borrower: str
    collateral_source: str
    ctf_token_id: int
    shares: int
    principal: int
    match_kickoff: int
    league_tier: int


@dataclass
class LoanRepaidEvent:
    loan_id: int
    principal_paid: int
    interest_paid: int


@dataclass
class LoanLiquidatedEvent:
    loan_id: int
    reason: str  # "ltv_breach" | "kickoff_due"
    actual_proceeds: int  # USDC, 6 decimals
    to_lp: int             # principal repaid to LP pool, 6 decimals
    to_treasury: int       # interest + penalty to treasury, 6 decimals
    residual_to_borrower: int  # whatever's left, 6 decimals


@dataclass
class LiquidationStartedEvent:
    loan_id: int
    current_price_e6: int
    expected_proceeds: int  # 6 decimals


@dataclass
class CtfReturnedFromKeeperEvent:
    loan_id: int


@dataclass
class TreasurySetEvent:
    previous: str
    next_: str


@dataclass
class TxResult:
    block_number: int
    success: bool
    loan_event: LoanOpenedEvent | None


@dataclass
class RepayResult:
    block_number: int
    success: bool
    repaid: LoanRepaidEvent | None


async def _rpc(rpc_url: str, method: str, params: list[Any]) -> Any:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "rpc error"))
    return body.get("result")


def _topic_to_address(topic_hex: str) -> str:
    """An indexed address topic is a 32-byte left-padded value; take the last 20 bytes."""
    h = topic_hex[2:] if topic_hex.startswith("0x") else topic_hex
    return "0x" + h[-40:]


def _topic_to_uint(topic_hex: str) -> int:
    return int(topic_hex, 16)


def _decode_loan_opened_log(log: dict) -> LoanOpenedEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 4 or topics[0].lower() != LOAN_OPENED_TOPIC0.lower():
        raise ValueError("not a LoanOpened log")
    loan_id = _topic_to_uint(topics[1])
    borrower = _topic_to_address(topics[2])
    collateral_source = _topic_to_address(topics[3])
    ctf_token_id, shares, principal, match_kickoff, league_tier = abi_decode(
        ["uint256", "uint256", "uint256", "uint256", "uint8"],
        bytes.fromhex(log["data"][2:] if log["data"].startswith("0x") else log["data"]),
    )
    return LoanOpenedEvent(
        loan_id=loan_id,
        borrower=borrower,
        collateral_source=collateral_source,
        ctf_token_id=ctf_token_id,
        shares=shares,
        principal=principal,
        match_kickoff=match_kickoff,
        league_tier=league_tier,
    )


def _decode_loan_repaid_log(log: dict) -> LoanRepaidEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 2 or topics[0].lower() != LOAN_REPAID_TOPIC0.lower():
        raise ValueError("not a LoanRepaid log")
    loan_id = _topic_to_uint(topics[1])
    principal_paid, interest_paid = abi_decode(
        ["uint256", "uint256"],
        bytes.fromhex(log["data"][2:] if log["data"].startswith("0x") else log["data"]),
    )
    return LoanRepaidEvent(
        loan_id=loan_id,
        principal_paid=principal_paid,
        interest_paid=interest_paid,
    )


def _decode_loan_liquidated_log(log: dict) -> LoanLiquidatedEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 2 or topics[0].lower() != LOAN_LIQUIDATED_TOPIC0.lower():
        raise ValueError("not a LoanLiquidated log")
    loan_id = _topic_to_uint(topics[1])
    reason, actual_proceeds, to_lp, to_treasury, residual = abi_decode(
        ["string", "uint256", "uint256", "uint256", "uint256"],
        bytes.fromhex(log["data"][2:] if log["data"].startswith("0x") else log["data"]),
    )
    return LoanLiquidatedEvent(
        loan_id=loan_id,
        reason=reason,
        actual_proceeds=actual_proceeds,
        to_lp=to_lp,
        to_treasury=to_treasury,
        residual_to_borrower=residual,
    )


def _decode_liquidation_started_log(log: dict) -> LiquidationStartedEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 2 or topics[0].lower() != LIQUIDATION_STARTED_TOPIC0.lower():
        raise ValueError("not a LiquidationStarted log")
    loan_id = _topic_to_uint(topics[1])
    current_price_e6, expected_proceeds = abi_decode(
        ["uint256", "uint256"],
        bytes.fromhex(log["data"][2:] if log["data"].startswith("0x") else log["data"]),
    )
    return LiquidationStartedEvent(
        loan_id=loan_id,
        current_price_e6=current_price_e6,
        expected_proceeds=expected_proceeds,
    )


def _decode_ctf_returned_log(log: dict) -> CtfReturnedFromKeeperEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 2 or topics[0].lower() != CTF_RETURNED_TOPIC0.lower():
        raise ValueError("not a CtfReturnedFromKeeper log")
    return CtfReturnedFromKeeperEvent(loan_id=_topic_to_uint(topics[1]))


def _decode_treasury_set_log(log: dict) -> TreasurySetEvent:
    topics: list[str] = log["topics"]
    if len(topics) != 3 or topics[0].lower() != TREASURY_SET_TOPIC0.lower():
        raise ValueError("not a TreasurySet log")
    return TreasurySetEvent(
        previous=_topic_to_address(topics[1]),
        next_=_topic_to_address(topics[2]),
    )


# Public dispatcher for the event indexer. Returns None for unknown topics
# (LP deposit/withdraw etc. land in lending_events as raw JSON without
# affecting Loan.status).
DecodedEvent = (
    LoanOpenedEvent
    | LoanRepaidEvent
    | LoanLiquidatedEvent
    | LiquidationStartedEvent
    | CtfReturnedFromKeeperEvent
    | TreasurySetEvent
)


def decode_event_log(log: dict) -> DecodedEvent | None:
    topics = log.get("topics") or []
    if not topics:
        return None
    topic0 = topics[0].lower()
    if topic0 == LOAN_OPENED_TOPIC0.lower():
        return _decode_loan_opened_log(log)
    if topic0 == LOAN_REPAID_TOPIC0.lower():
        return _decode_loan_repaid_log(log)
    if topic0 == LOAN_LIQUIDATED_TOPIC0.lower():
        return _decode_loan_liquidated_log(log)
    return None


# Map our white-listed event types to (topic0, decoder). Event indexer uses
# this to filter `eth_getLogs` results and dispatch decode.
EVENT_TOPIC_MAP: dict[str, str] = {
    "LoanOpened": LOAN_OPENED_TOPIC0,
    "LoanRepaid": LOAN_REPAID_TOPIC0,
    "LoanLiquidated": LOAN_LIQUIDATED_TOPIC0,
}


def event_type_for_topic0(topic0: str) -> str | None:
    """Reverse-lookup `LendingEvent.event_type` for a raw topic0 hex."""
    target = topic0.lower()
    for name, t0 in EVENT_TOPIC_MAP.items():
        if t0.lower() == target:
            return name
    return None


async def wait_for_loan_opened(
    rpc_url: str,
    tx_hash: str,
    vault_address: str,
    *,
    timeout_s: int = 60,
    poll_every_s: float = 2.0,
) -> TxResult:
    """Poll until the tx is mined; return the decoded LoanOpened event (if any)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        receipt = await _rpc(rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            success = receipt.get("status") in ("0x1", 1, "0x01")
            block_number = int(receipt.get("blockNumber", "0x0"), 16)
            event: LoanOpenedEvent | None = None
            if success:
                vault_lc = vault_address.lower()
                for log in receipt.get("logs", []):
                    if log.get("address", "").lower() != vault_lc:
                        continue
                    topics = log.get("topics") or []
                    if topics and topics[0].lower() == LOAN_OPENED_TOPIC0.lower():
                        try:
                            event = _decode_loan_opened_log(log)
                        except Exception as e:
                            logger.warning("decode LoanOpened failed: %s", e)
                        break
            return TxResult(
                block_number=block_number, success=success, loan_event=event
            )
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"Tx {tx_hash} not mined within {timeout_s}s")
        await asyncio.sleep(poll_every_s)


async def wait_for_loan_repaid(
    rpc_url: str,
    tx_hash: str,
    vault_address: str,
    *,
    timeout_s: int = 60,
    poll_every_s: float = 2.0,
) -> RepayResult:
    """Poll for the LoanRepaid event."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        receipt = await _rpc(rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            success = receipt.get("status") in ("0x1", 1, "0x01")
            block_number = int(receipt.get("blockNumber", "0x0"), 16)
            event: LoanRepaidEvent | None = None
            if success:
                vault_lc = vault_address.lower()
                for log in receipt.get("logs", []):
                    if log.get("address", "").lower() != vault_lc:
                        continue
                    topics = log.get("topics") or []
                    if topics and topics[0].lower() == LOAN_REPAID_TOPIC0.lower():
                        try:
                            event = _decode_loan_repaid_log(log)
                        except Exception as e:
                            logger.warning("decode LoanRepaid failed: %s", e)
                        break
            return RepayResult(
                block_number=block_number, success=success, repaid=event
            )
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"Tx {tx_hash} not mined within {timeout_s}s")
        await asyncio.sleep(poll_every_s)
