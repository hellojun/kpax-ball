from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, index=True)
    outcome: Mapped[str] = mapped_column(String(16))  # "home" | "away" | "draw"
    home_goals: Mapped[int] = mapped_column(Integer)
    away_goals: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True)
    market_id: Mapped[int] = mapped_column(Integer, index=True)

    # Accuracy metrics
    correct_outcome: Mapped[bool] = mapped_column()  # Top prediction matched?
    brier_score: Mapped[float] = mapped_column(Float)  # Probability calibration
    deviation_correct: Mapped[bool | None] = mapped_column(nullable=True)  # "Overpriced" call correct?

    verified_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
