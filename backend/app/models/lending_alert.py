"""LendingAlert — 预警幂等记录。

每条 (loan_id, alert_type) 只发一次，避免重复骚扰用户。
`delivered_at` 标记客户端是否已 ack（chrome.notifications + 轮询模型，详见
plan v0.2 §3 Day 4）。NULL = 待推送；非空 = 已下发到用户端。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

LENDING_ALERT_TYPES = ("ltv_70", "ltv_80", "kickoff_4h", "kickoff_2h")


class LendingAlert(Base):
    __tablename__ = "lending_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("loans.id"), nullable=False, index=True
    )
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("loan_id", "alert_type", name="uq_lending_alerts_loan_type"),
    )
