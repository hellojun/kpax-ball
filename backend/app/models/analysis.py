from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, index=True)
    analysis_type: Mapped[str] = mapped_column(String(32))  # "preview" | "deep"

    # KPAX predictions
    kpax_odds_home: Mapped[float] = mapped_column(Float)
    kpax_odds_away: Mapped[float] = mapped_column(Float)
    kpax_odds_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16))  # "high" | "medium" | "low"

    # Full report JSON (for deep analysis)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    model_used: Mapped[str] = mapped_column(String(64))
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
