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


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport", "canonical_name", name="uq_team_canonical"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    canonical_name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    league_hint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("sport", "alias_key", name="uq_team_alias_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    alias: Mapped[str] = mapped_column(String(120), index=True)
    alias_key: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    model_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    pick: Mapped[str] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float)
    edge_score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(30))
    reasoning: Mapped[str] = mapped_column(Text)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    engine_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UserPrediction(Base):
    __tablename__ = "user_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    pick: Mapped[str] = mapped_column(String(120))
    analysis_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    stake_units: Mapped[float] = mapped_column(Float, default=10.0)
    is_settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profit_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CommunityComment(Base):
    __tablename__ = "community_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    comment_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CommunityReaction(Base):
    __tablename__ = "community_reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    reaction: Mapped[str] = mapped_column(String(30), default="like", index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CommunityPlay(Base):
    __tablename__ = "community_plays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    stake_units: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(30), default="tailed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class WinSlip(Base):
    __tablename__ = "win_slips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    proof_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    profit_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    prediction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    phase: Mapped[str] = mapped_column(String(30), index=True)  # initial, published, closing
    market: Mapped[str] = mapped_column(String(50), index=True)
    bookmaker: Mapped[str | None] = mapped_column(String(80), nullable=True)
    home_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    draw_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    over_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="fixture")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    model_type: Mapped[str] = mapped_column(String(50))
    split_strategy: Mapped[str] = mapped_column(String(80), default="walk_forward")
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MatchEvent(Base):
    """Live match events: goals, cards, substitutions, lineups.

    Stored per-fixture so the SSE stream and notification engine can
    read new rows since the last poll. Each event is idempotent on
    (fixture_id, event_type, minute, team, player) to avoid duplicates
    from repeated score-sync calls.
    """

    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "event_type", "minute", "team", "player",
            name="uq_match_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    # goal | yellow_card | red_card | substitution | lineup | var | penalty_missed
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    player: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assist: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(120), nullable=True)   # "Normal Goal", "Own Goal", "Yellow Card", etc.
    home_score_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MatchLineup(Base):
    """Starting XI + bench for each team in a fixture."""

    __tablename__ = "match_lineups"
    __table_args__ = (
        UniqueConstraint("fixture_id", "team", "player", name="uq_lineup_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    team: Mapped[str] = mapped_column(String(120), index=True)
    player: Mapped[str] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(40), nullable=True)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=True)
    formation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PushSubscription(Base):
    """Web Push / notification subscriptions from browser clients."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys_p256dh: Mapped[str | None] = mapped_column(Text, nullable=True)
    keys_auth: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    fixture_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # subscribed fixtures
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InsiderSignal(Base):
    """Insider market intelligence — injury news, sharp line movements, weather context.

    Stored per-fixture so the ML feature pipeline can join them at prediction time.
    signal_type values:
      injury_home | injury_away   — key player out / doubtful
      sharp_line_move             — significant closing-line movement toward a side
      weather                     — wind / rain / extreme heat context
      referee                     — referee card/foul rate tendencies
      public_betting              — % of tickets vs money on each side
    """

    __tablename__ = "insider_signals"
    __table_args__ = (
        UniqueConstraint("fixture_id", "signal_type", "source", name="uq_insider_signal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    signal_type: Mapped[str] = mapped_column(String(40), index=True)
    # Numeric value (e.g. line move = -0.5, wind_speed_mph = 22, card_rate = 3.4)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Direction context: "home", "away", "neutral"
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Human-readable description e.g. "Haaland doubtful - hamstring"
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
