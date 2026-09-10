"""Quarantine table for provider fixtures that fail quality validation."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RejectedFixture(Base):
    """Quarantine for provider rows that failed fixture quality validation."""

    __tablename__ = "rejected_fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), index=True, default="unknown")
    raw_home: Mapped[str] = mapped_column(String(200), default="")
    raw_away: Mapped[str] = mapped_column(String(200), default="")
    reason: Mapped[str] = mapped_column(String(80), index=True, default="unknown")
    sport: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    league: Mapped[str | None] = mapped_column(String(80), nullable=True)
    match_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
