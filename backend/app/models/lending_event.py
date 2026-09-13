"""LendingEvent — LendingVault 合约事件的链下落库（审计 + 回查）。

EventIndexer 订阅合约日志，每条日志落一行；通过 (tx_hash, log_index) 唯一
索引实现幂等。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 事件类型白名单
LENDING_EVENT_TYPES = (
    "LoanOpened",
    "LoanRepaid",
    "LoanLiquidated",
    "LPDeposit",
    "LPWithdraw",
)


class LendingEvent(Base):
    __tablename__ = "lending_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("loans.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", name="uq_lending_events_log"),
    )
