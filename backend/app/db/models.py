from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    prediction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    phase: Mapped[str] = mapped_column(String(30), index=True)
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
    model_type: Mapped[str] = mapped_column(String(120))
    path: Mapped[str] = mapped_column(String(255))
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelArtifact(Base):
    """Durable model bytes stored in PostgreSQL so restarts cannot erase models."""

    __tablename__ = "model_artifacts"
    __table_args__ = (UniqueConstraint("sport", "filename", name="uq_model_artifact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    model_type: Mapped[str] = mapped_column(String(120), default="uploaded")
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MarketEvidence(Base):
    """Durable per (sport, market) publication evidence.

    Tracks settled prediction outcomes so public publication is gated on
    empirical evidence rather than generic confidence alone. A market with
    insufficient sample size, poor recent accuracy, or repeated losses is
    blocked from public publication while remaining internally evaluated.
    """

    __tablename__ = "market_evidence"
    __table_args__ = (UniqueConstraint("sport", "market", name="uq_market_evidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    settled: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    pushes: Mapped[int] = mapped_column(Integer, default=0)
    recent_settled: Mapped[int] = mapped_column(Integer, default=0)
    recent_wins: Mapped[int] = mapped_column(Integer, default=0)
    recent_losses: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_loss_streak: Mapped[int] = mapped_column(Integer, default=0)
    publication_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_model_trained: Mapped[bool] = mapped_column(Boolean, default=False)
    historical_settled: Mapped[int] = mapped_column(Integer, default=0)
    historical_wins: Mapped[int] = mapped_column(Integer, default=0)
    historical_losses: Mapped[int] = mapped_column(Integer, default=0)
    historical_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_brier_sum: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_brier_count: Mapped[int] = mapped_column(Integer, default=0)
    historical_odds_count: Mapped[int] = mapped_column(Integer, default=0)
    historical_roi_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_has_odds: Mapped[bool] = mapped_column(Boolean, default=False)
    bootstrap_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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
    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint("fixture_id", "event_type", "minute", "team", "player", name="uq_match_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    player: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assist: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(120), nullable=True)
    home_score_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MatchLineup(Base):
    __tablename__ = "match_lineups"
    __table_args__ = (UniqueConstraint("fixture_id", "team", "player", name="uq_lineup_player"),)

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
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys_p256dh: Mapped[str | None] = mapped_column(Text, nullable=True)
    keys_auth: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    fixture_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InsiderSignal(Base):
    __tablename__ = "insider_signals"
    __table_args__ = (UniqueConstraint("fixture_id", "signal_type", "source", name="uq_insider_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    signal_type: Mapped[str] = mapped_column(String(40), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ModelFeedback(Base):
    """Structured post-match feedback record for one settled prediction.

    Created at settlement time from real data only. ``successful_signals`` /
    ``failed_signals`` / ``error_type`` are derived strictly from the stored
    feature snapshot, match events, and market context — never invented. One
    prediction = one feedback row (unique ``prediction_id``). A single match
    never rewrites model weights; ``feedback_status`` marks whether a repeated
    pattern has been promoted into calibration (``validated``) or not.
    """

    __tablename__ = "model_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    league: Mapped[str | None] = mapped_column(String(80), nullable=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    pick: Mapped[str] = mapped_column(String(120))
    predicted_probability: Mapped[float] = mapped_column(Float)
    actual_result: Mapped[str] = mapped_column(String(10), index=True)
    probability_error: Mapped[float] = mapped_column(Float, default=0.0)
    brier_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[str | None] = mapped_column(String(30), nullable=True)
    outcome_text: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    error_classifications: Mapped[list | None] = mapped_column(JSON, nullable=True)
    signal_attribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    defense_strong: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    successful_signals: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failed_signals: Mapped[list | None] = mapped_column(JSON, nullable=True)
    contributing_factors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    feedback_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    validated_pattern: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UserFollow(Base):
    """Lightweight follow infrastructure (teams / leagues / sports).

    Anonymous browsing stays fully public; follows only affect personalized
    feed ordering for a self-declared username. Never blocks public content.
    """

    __tablename__ = "user_follows"
    __table_args__ = (
        UniqueConstraint("username", "entity_type", "entity_value", name="uq_user_follow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_value: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HistoricalEvaluation(Base):
    """Walk-forward out-of-sample evaluation of historical fixtures.

    Every record represents one (fixture, market) scored by a fold model that
    was trained on strictly prior data — no in-sample leakage. These rows
    feed the market gate's evidence engine without touching the live
    Prediction table or publication balance.
    """

    __tablename__ = "historical_evaluation"
    __table_args__ = (
        UniqueConstraint("fixture_id", "market", "model_version_id", "fold_index", name="uq_historical_eval"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    sport: Mapped[str] = mapped_column(String(30), index=True)
    league: Mapped[str] = mapped_column(String(80), nullable=True)
    match_date: Mapped[date] = mapped_column(Date, index=True)
    home_team: Mapped[str] = mapped_column(String(120))
    away_team: Mapped[str] = mapped_column(String(120))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market: Mapped[str] = mapped_column(String(50), index=True)
    pick: Mapped[str] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float)
    edge_score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_odds: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    inference_mode: Mapped[str] = mapped_column(String(40), default="walk_forward_fold")
    fold_index: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="bootstrap")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
