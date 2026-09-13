"""TosAcceptance — 用户签署 KPAX Lending Terms of Service 的记录。

D8：首次借款前必须签署；ToS 版本变更时重新弹框。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TosAcceptance(Base):
    __tablename__ = "lending_tos_acceptance"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    tos_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # MVP 记 "clicked"；Phase 2 可存 EIP-712 签名 hex
    signature: Mapped[str] = mapped_column(String(512), nullable=False, default="clicked")
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "tos_version", name="uq_tos_user_version"),
    )
