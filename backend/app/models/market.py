from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    condition_id: Mapped[str] = mapped_column(String(128))
    home_team: Mapped[str] = mapped_column(String(128))
    away_team: Mapped[str] = mapped_column(String(128))
    competition: Mapped[str] = mapped_column(String(128))
    market_type: Mapped[str] = mapped_column(String(64), default="match_winner")
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(index=True)
    polymarket_odds_home: Mapped[float] = mapped_column(Float)
    polymarket_odds_away: Mapped[float] = mapped_column(Float)
    polymarket_odds_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
