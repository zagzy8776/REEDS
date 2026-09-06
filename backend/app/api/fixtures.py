"""Public fixture and feed-status API."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()


def _serialize_fixture(fx: Fixture) -> dict:
    has_odds = any(value is not None for value in (fx.home_odds, fx.draw_odds, fx.away_odds))
    completed = fx.home_score is not None and fx.away_score is not None
    extra = fx.extra if isinstance(fx.extra, dict) else {}
    total_goals = None if not completed else fx.home_score + fx.away_score
    status = str(extra.get("status") or "pending")

    if completed:
        result_label = "completed"
    elif extra.get("live") or status.upper() in {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}:
        result_label = "live"
    elif fx.match_date == date.today():
        result_label = "today"
    elif fx.match_date > date.today():
        result_label = "upcoming"
    else:
        result_label = "past"

    return {
        "id": fx.id,
        "sport": fx.sport,
        "league": fx.league,
        "season": fx.season,
        "match_date": fx.match_date,
        "home_team": fx.home_team,
        "away_team": fx.away_team,
        "home_score": fx.home_score,
        "away_score": fx.away_score,
        "home_odds": fx.home_odds,
        "draw_odds": fx.draw_odds,
        "away_odds": fx.away_odds,
        "has_odds": has_odds,
        "total_goals": total_goals,
        "api_status": status,
        "result_label": result_label,
        "source": fx.source,
        "extra": extra,
    }


@router.get("/fixtures/upcoming")
def upcoming_fixtures(
    scope: str = "upcoming",
    sport: str | None = None,
    league: str | None = None,
    limit: int = 300,
    db: Session = Depends(get_db),
):
    """Return the public fixture board with predictable scope semantics."""

    today = date.today()
    scope = (scope or "upcoming").lower()
    limit = max(1, min(limit, 500))

    query = db.query(Fixture)
    if sport:
        query = query.filter(Fixture.sport == sport)
    if league:
        query = query.filter(Fixture.league.ilike(f"%{league.strip()}%"))

    if scope in {"live", "today"}:
        query = query.filter(Fixture.match_date == today)
    elif scope == "results":
        query = query.filter(Fixture.match_date < today)
    elif scope == "all":
        query = query.filter(Fixture.match_date >= today)
    else:
        query = query.filter(Fixture.match_date >= today)

    if scope in {"live", "today"}:
        order = [Fixture.match_date.asc(), Fixture.id.asc()]
    elif scope == "results":
        order = [Fixture.match_date.desc(), Fixture.id.desc()]
    else:
        order = [Fixture.match_date.asc(), Fixture.id.asc()]

    fixtures = query.order_by(*order).limit(limit).all()

    # Predictions and fixtures live in the same table, so when the prediction
    # board is healthy but an over-filtered/stale fixture query comes back empty,
    # reuse the exact fixtures that currently back published picks. This makes
    # the match centre consistent with the AI board instead of showing nothing.
    if not fixtures and scope not in {"results"}:
        prediction_query = (
            db.query(Fixture)
            .join(Prediction, Prediction.fixture_id == Fixture.id)
            .filter(
                Prediction.is_published == True,
                Prediction.status == "active",
                Fixture.match_date >= today,
            )
        )
        if sport:
            prediction_query = prediction_query.filter(Fixture.sport == sport)
        if league:
            prediction_query = prediction_query.filter(Fixture.league.ilike(f"%{league.strip()}%"))
        fixtures = (
            prediction_query
            .distinct(Fixture.id)
            .order_by(Fixture.match_date.asc(), Fixture.id.asc())
            .limit(limit)
            .all()
        )
        if fixtures:
            log.warning(
                "Fixture board recovered %d rows directly from active predictions",
                len(fixtures),
            )

    if not fixtures and scope not in {"results"} and not sport and not league:
        # Fast last-resort recovery: OpenFoot exposes public fixture reads, so
        # a newly-awakened Render instance can populate the board immediately
        # without waiting for the background coverage worker or another cron.
        try:
            from app.scraper.coverage_sources import ingest_openfoot_football

            recovery_dates = [
                (today + timedelta(days=offset)).isoformat()
                for offset in range(3)
            ]
            recovered = ingest_openfoot_football(db, None, recovery_dates)
            if recovered:
                fixtures = (
                    db.query(Fixture)
                    .filter(Fixture.match_date >= today)
                    .order_by(Fixture.match_date.asc(), Fixture.id.asc())
                    .limit(limit)
                    .all()
                )
                log.info("Public fixture endpoint recovered %d rows from OpenFoot", recovered)
        except Exception:
            log.exception("OpenFoot emergency fixture recovery failed")

        # Also queue the normal full multi-provider refresh. This keeps the DB
        # populated beyond the small synchronous recovery window.
        try:
            from app.services.coverage_runner import start_coverage_refresh
            start_coverage_refresh(reason="public_fixture_board_empty")
        except Exception:
            log.exception("Could not queue fixture coverage recovery")

    return [_serialize_fixture(fx) for fx in fixtures]


@router.get("/fixtures/status")
def fixtures_status(db: Session = Depends(get_db)):
    """Compact diagnostics for the public match-center feed."""

    today = date.today()
    horizon = today + timedelta(days=7)
    base = db.query(Fixture).filter(Fixture.match_date >= today, Fixture.match_date <= horizon)
    total = base.count()
    leagues = db.query(func.count(func.distinct(Fixture.league))).filter(
        Fixture.match_date >= today,
        Fixture.match_date <= horizon,
    ).scalar() or 0
    with_odds = base.filter(
        (Fixture.home_odds.isnot(None))
        | (Fixture.draw_odds.isnot(None))
        | (Fixture.away_odds.isnot(None))
    ).count()
    with_scores = base.filter(
        Fixture.home_score.isnot(None), Fixture.away_score.isnot(None)
    ).count()

    if total == 0:
        feed_health = "empty"
    elif total < 10:
        feed_health = "degraded"
    else:
        feed_health = "active"

    return {
        "feed_health": feed_health,
        "api_rows": total,
        "sample_rows": total,
        "with_scores": with_scores,
        "with_odds": with_odds,
        "leagues": leagues,
        "window_days": 7,
        "today": today,
        "source_counts": {
            source: count
            for source, count in db.query(Fixture.source, func.count(Fixture.id))
            .filter(Fixture.match_date >= today, Fixture.match_date <= horizon)
            .group_by(Fixture.source)
            .all()
        },
    }


@router.get("/fixtures/{fixture_id}")
def fixture_detail(fixture_id: int, db: Session = Depends(get_db)):
    fx = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    if not fx:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return _serialize_fixture(fx)
