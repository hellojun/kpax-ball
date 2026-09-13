"""Loan — KPAX Lending 借款核心表。

每行对应 LendingVault 合约上的一笔 loan，也记录 AI 建议、kickoff 时间等
链下元信息，用于 Keeper 扫描、前端展示、坏账分析。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 状态机（Sprint 4 V2 — 4 步真实清算）:
#  pending             —— prepare-borrow 已返回但 confirm 未到
#  active              —— 链上借款已开、Keeper 在监控
#  withdrawing         —— V2: keeper 已 withdrawCtfForLiquidation，CTF 转出 vault，
#                          等 Polymarket 卖出 + transfer USDC + settle
#  liquidating         —— V2: settleLiquidation tx 已发，等链上 LoanLiquidated 落库
#  repaid              —— 用户还清
#  liquidated_ltv      —— LTV ≥ liquidation_ltv 触发强平（终态）
#  liquidated_kickoff  —— kickoff - 2h 触发强平（终态）
#  closed              —— 预留，用户主动全额结清并取回抵押
#  failed              —— prepare-borrow → confirm 失败 / 链上 revert
LOAN_STATUSES = (
    "pending",
    "active",
    "withdrawing",
    "liquidating",
    "repaid",
    "liquidated_ltv",
    "liquidated_kickoff",
    "closed",
    "failed",
)


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Borrower
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    wallet_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Collateral（Polymarket CTF ERC-1155）
    ctf_token_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    market_slug: Mapped[str] = mapped_column(String(256), nullable=False)
    home_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    competition: Mapped[str | None] = mapped_column(String(128), nullable=True)
    collateral_shares: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)
    collateral_value_at_open: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)

    # Loan terms
    principal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    apr_bps: Mapped[int] = mapped_column(Integer, nullable=False)  # 1200 = 12%
    league_tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 / 2 / 3
    opened_ltv: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    # Status & on-chain binding
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    onchain_loan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    close_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Set by keeper when liquidate tx is sent. Reaper uses this to detect
    # stuck `liquidating` rows (>15 min without LoanLiquidated event) and
    # roll back to `active` so the next tick can retry.
    liquidating_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # V2 (Sprint 4): set when keeper has called withdrawCtfForLiquidation and
    # the CTF is in the keeper EOA waiting to be sold on Polymarket. NULL
    # while the loan is in any other state. Reaper alerts on
    # `withdrawing AND withdrawn_at < now-30min` so admin can step in if
    # the off-chain sell got stuck.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timeline
    opened_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    match_kickoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Settlement snapshot（仅在 closed 后填）
    total_interest_paid: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    liquidation_penalty: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    residual_to_user: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)

    # AI 元数据（用于回训 & 采纳率统计）
    ai_recommended_ltv: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    ai_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_loans_status_kickoff", "status", "match_kickoff_at"),
        Index("ix_loans_status_ctf_token", "status", "ctf_token_id"),
        Index("ix_loans_user_status", "user_id", "status"),
    )
