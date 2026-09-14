"""Fixture sanitizer + prediction readiness gate.

Garbage from providers (cookie banners, privacy text, ads) must never enter
the fixtures table or the prediction engine. Invalid rows are quarantined in
rejected_fixtures for auditability.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

BAD_WORDS = [
    "advertising",
    "advertisement",
    "content",
    "cookie",
    "privacy",
    "checkbox",
    "javascript",
    "terms",
    "illustration",
    "settings",
    "subscribe",
    "newsletter",
    "click here",
    "accept all",
    "sign up",
    "log in",
    "login",
    "gdpr",
    "consent",
    "user id",
    "legitima",
    "object to",
    "view illustrations",
    "livescore",
    "webpage",
    "identifier",
    "precise location",
    "non vs",
    "advertising content",
    "intended audience",
    "floating icon",
    "this is very helpful",
]


def clean_team_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = str(name).strip()
    if not cleaned:
        return None
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 60 or len(cleaned) < 2:
        return None
    lowered = cleaned.lower()
    for word in BAD_WORDS:
        if word in lowered:
            return None
    alpha = sum(1 for ch in cleaned if ch.isalpha())
    if alpha < 2:
        return None
    if any(tok in cleaned for tok in ("**", "#####", "http://", "https://", "www.", "[", "]")):
        return None
    if cleaned.count(" ") >= 6:
        return None
    return cleaned


def validate_fixture(home: str | None, away: str | None, sport: str | None = None) -> dict:
    home_clean = clean_team_name(home)
    away_clean = clean_team_name(away)
    if not home_clean or not away_clean:
        return {"valid": False, "reason": "invalid_team_name"}
    if home_clean.lower() == away_clean.lower():
        return {"valid": False, "reason": "same_team"}
    return {
        "valid": True,
        "home": home_clean,
        "away": away_clean,
        "sport": (sport or "").strip().lower() or None,
    }


def save_rejected_fixture(
    db: Session,
    *,
    provider: str | None,
    raw_home: str | None,
    raw_away: str | None,
    reason: str,
    sport: str | None = None,
    league: str | None = None,
    match_date=None,
    raw_payload: dict | None = None,
) -> None:
    from app.db.rejected_fixture import RejectedFixture  # type: ignore
    try:
        row = RejectedFixture(
            provider=provider,
            raw_home=str(raw_home)[:200] if raw_home else None,
            raw_away=str(raw_away)[:200] if raw_away else None,
            reason=reason,
            sport=sport,
            league=league,
            match_date=match_date,
            raw_payload=raw_payload,
        )
        db.add(row)
        db.commit()
    except Exception:
        log.exception("failed to save rejected fixture")
        try:
            db.rollback()
        except Exception:
            pass


def _team_history_count(db: Session, sport: str, home_team: str, away_team: str) -> int:
    from app.db.models import Fixture
    from sqlalchemy import or_

    q = (
        db.query(Fixture)
        .filter(
            Fixture.sport == sport,
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            or_(
                Fixture.home_team == home_team,
                Fixture.away_team == home_team,
                Fixture.home_team == away_team,
                Fixture.away_team == away_team,
            ),
        )
        .count()
    )
    return int(q or 0)


def prediction_readiness(db: Session, fixture) -> dict:
    """STRIPPED gate: allow model prediction whenever the fixture is real.

    Removed hard requirements for:
      - odds
      - historical team data
      - market sample / accuracy gates (handled elsewhere if at all)

    Only blocks pure garbage team names. Soccer tries registry + MODEL_DIR
    for a model file; if missing, still marks ready so GenericSportEngine
    can produce a lean (fixture_prediction will fall through).
    """
    from app.services.model_registry import active_model_path

    reasons: list[str] = []
    notes: list[str] = []
    failure_mode = None
    checks = {
        "fixture_found": fixture is not None,
        "league_identified": bool(getattr(fixture, "league", None)) if fixture else False,
        "teams_valid": False,
        "odds_present": False,
        "history_present": False,
        "model_available": False,
    }
    if fixture is None:
        return {
            "ready": False,
            "reason": ["Fixture not found"],
            "failure_mode": "fixture_not_found",
            "checks": checks,
            "history_count": 0,
            "odds_optional": True,
            "gates_stripped": True,
        }

    quality = validate_fixture(fixture.home_team, fixture.away_team, fixture.sport)
    checks["teams_valid"] = bool(quality.get("valid"))
    if not quality.get("valid"):
        h = str(fixture.home_team or "").strip()
        a = str(fixture.away_team or "").strip()
        if len(h) >= 2 and len(a) >= 2 and h.lower() != a.lower():
            checks["teams_valid"] = True
            notes.append(f"teams soft-accepted despite validate: {quality.get('reason')}")
        else:
            failure_mode = "invalid_teams"
            reasons.append(f"Invalid fixture teams ({quality.get('reason', 'unknown')})")

    has_odds = any(
        v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)
    )
    checks["odds_present"] = has_odds
    if not has_odds:
        notes.append("No odds (stripped — not required)")

    sport = str(fixture.sport or "").strip().lower()
    history_count = 0
    try:
        history_count = _team_history_count(
            db, sport, str(fixture.home_team or ""), str(fixture.away_team or "")
        )
    except Exception:
        log.exception("history count failed for fixture %s", getattr(fixture, "id", None))
    checks["history_present"] = history_count > 0
    if history_count <= 0:
        notes.append("No history (stripped — not required)")

    if sport == "soccer":
        try:
            model_path = active_model_path(db, sport)
            checks["model_available"] = model_path is not None
            if not model_path:
                import os
                from pathlib import Path as P
                try:
                    from app.core.config import get_settings
                    settings = get_settings()
                    model_dir = P(getattr(settings, "model_dir", None) or os.getenv("MODEL_DIR") or "data/models")
                except Exception:
                    model_dir = P(os.getenv("MODEL_DIR") or "data/models")
                if model_dir.is_dir():
                    cands = list(model_dir.rglob("*.joblib")) + list(model_dir.rglob("*.pkl"))
                    checks["model_available"] = any(c.is_file() for c in cands)
                    if checks["model_available"]:
                        notes.append(f"model found under {model_dir}")
            if not checks["model_available"]:
                notes.append("No soccer model file — will use GenericSportEngine fallback")
        except Exception:
            log.exception("model check failed fixture %s", getattr(fixture, "id", None))
            notes.append("model check error — still allowing prediction")
            checks["model_available"] = False
    else:
        checks["model_available"] = True

    if not checks["league_identified"]:
        notes.append("League empty (stripped — not required)")

    ready = bool(checks["teams_valid"])

    result = {
        "ready": ready,
        "reason": reasons,
        "notes": notes,
        "failure_mode": failure_mode,
        "checks": checks,
        "history_count": history_count,
        "odds_optional": True,
        "gates_stripped": True,
    }
    log.info(
        "prediction_readiness STRIPPED fixture=%s ready=%s teams=%s history=%d odds=%s model=%s",
        getattr(fixture, "id", None),
        ready,
        checks["teams_valid"],
        history_count,
        has_odds,
        checks.get("model_available"),
    )
    return result
