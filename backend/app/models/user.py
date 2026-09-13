from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Privy 用户 ID，格式 "did:privy:xxx"
    privy_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    # Polygon 钱包地址 (Privy MPC 生成，KPAX 拿不到私钥)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True)

    # 可选，Privy 返回的 email (若用户用 Email / Google 登录)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
