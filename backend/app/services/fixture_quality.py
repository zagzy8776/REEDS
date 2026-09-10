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
]


def clean_team_name(name: str | None) -> str | None:
    """Return a cleaned team name, or None if it looks like provider garbage."""
    if not name:
        return None

    cleaned = str(name).strip()
    if not cleaned:
        return None

    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 60:
        return None
    if len(cleaned) < 2:
        return None

    lowered = cleaned.lower()
    for word in BAD_WORDS:
        if word in lowered:
            return None

    alpha = sum(1 for ch in cleaned if ch.isalpha())
    if alpha < 2:
        return None

    return cleaned


def validate_fixture(home: str | None, away: str | None, sport: str | None = None) -> dict:
    """Validate a provider fixture before DB write.

    Returns:
      {"valid": True, "home": str, "away": str}
      {"valid": False, "reason": str}
    """
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
    """Persist a quarantined provider row for audit (never silent-drop)."""
    from app.db.models import RejectedFixture

    row = RejectedFixture(
        provider=str(provider or "unknown")[:80],
        raw_home=str(raw_home or "")[:200],
        raw_away=str(raw_away or "")[:200],
        reason=str(reason or "unknown")[:80],
        sport=str(sport or "")[:30] if sport else None,
        league=str(league or "")[:80] if league else None,
        match_date=match_date,
        raw_payload=raw_payload if isinstance(raw_payload, dict) else None,
        created_at=datetime.utcnow(),
    )
    try:
        db.add(row)
        db.flush()
    except Exception:
        log.exception(
            "Failed to quarantine rejected fixture provider=%s home=%s away=%s reason=%s",
            provider,
            raw_home,
            raw_away,
            reason,
        )


def prediction_readiness(db: Session, fixture) -> dict:
    """Evidence checklist before AI Reads generation / publication.

    Does not run the model — only reports whether the fixture has enough
    match-specific evidence to justify analysis.
    """
    from app.db.models import Fixture

    reasons: list[str] = []
    checks = {
        "fixture_found": fixture is not None,
        "league_identified": bool(getattr(fixture, "league", None)),
        "teams_valid": False,
        "odds_present": False,
        "history_present": False,
    }

    if fixture is None:
        return {
            "ready": False,
            "reason": ["Fixture not found"],
            "checks": checks,
        }

    quality = validate_fixture(fixture.home_team, fixture.away_team, fixture.sport)
    checks["teams_valid"] = bool(quality.get("valid"))
    if not quality.get("valid"):
        reasons.append(f"Invalid fixture teams ({quality.get('reason', 'unknown')})")

    has_odds = any(
        v is not None
        for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds)
    )
    checks["odds_present"] = has_odds
    if not has_odds:
        reasons.append("No odds market")

    history_count = 0
    try:
        home = str(fixture.home_team or "")
        away = str(fixture.away_team or "")
        sport = str(fixture.sport or "")
        history_count = (
            db.query(Fixture)
            .filter(
                Fixture.sport == sport,
                Fixture.home_score.isnot(None),
                Fixture.away_score.isnot(None),
                (
                    (Fixture.home_team == home)
                    | (Fixture.away_team == home)
                    | (Fixture.home_team == away)
                    | (Fixture.away_team == away)
                ),
            )
            .limit(5)
            .count()
        )
    except Exception:
        log.exception("history count failed for fixture %s", getattr(fixture, "id", None))

    checks["history_present"] = history_count > 0
    if history_count <= 0:
        reasons.append("Insufficient team form / no completed history")

    ready = checks["teams_valid"] and checks["league_identified"] and (
        checks["odds_present"] or checks["history_present"]
    )
    if not checks["league_identified"]:
        reasons.append("League not identified")

    return {
        "ready": ready,
        "reason": reasons,
        "checks": checks,
        "history_count": history_count,
    }
