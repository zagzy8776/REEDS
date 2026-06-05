from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (UniqueConstraint("sport", "league", "match_date", "home_team", "away_team", name="uq_fixture"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    league: Mapped[str] = mapped_column(String(80), index=True)
    season: Mapped[str] = mapped_column(String(20), index=True)
    match_date: Mapped[date] = mapped_column(Date, index=True)
    home_team: Mapped[str] = mapped_column(String(120), index=True)
    away_team: Mapped[str] = mapped_column(String(120), index=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    draw_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    pick: Mapped[str] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float)
    edge_score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(30))
    reasoning: Mapped[str] = mapped_column(Text)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    engine_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    model_type: Mapped[str] = mapped_column(String(50))
    path: Mapped[str] = mapped_column(String(255))
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(30), default="free")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
