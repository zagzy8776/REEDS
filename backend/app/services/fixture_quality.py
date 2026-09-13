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
    from app.db.rejected_fixture import RejectedFixture

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
            provider, raw_home, raw_away, reason,
        )


def _team_history_count(db: Session, sport: str, home: str, away: str) -> int:
    """Count completed fixtures involving either team, resolved through aliases.

    Exact string matching on home_team/away_team is unreliable: ingestion
    canonicalises names via resolve_team_name, and special-character encodings
    differ between providers.  Resolving both the query names and the stored
    names through the TeamAlias table makes the check tolerant of spelling,
    accent, and abbreviation variance.
    """
    from app.db.models import Team, TeamAlias
    from app.services.data_quality import alias_key

    def _canonical(raw: str) -> str | None:
        key = alias_key(raw)
        if not key:
            return None
        alias = (
            db.query(TeamAlias)
            .filter(TeamAlias.sport == sport, TeamAlias.alias_key == key)
            .first()
        )
        if alias:
            team = db.query(Team).filter(Team.id == alias.team_id).first()
            if team:
                return team.canonical_name
        return None

    targets: list[str] = []
    for raw in (home, away):
        canonical = _canonical(raw)
        if canonical and canonical not in targets:
            targets.append(canonical)

    if not targets:
        return 0

    from app.db.models import Fixture
    query = (
        db.query(Fixture)
        .filter(
            Fixture.sport == sport,
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
        )
    )
    conditions = []
    for name in targets:
        conditions.append(Fixture.home_team == name)
        conditions.append(Fixture.away_team == name)
    if len(conditions) > 1:
        from sqlalchemy import or_
        query = query.filter(or_(*conditions))
    else:
        query = query.filter(conditions[0])
    return query.count()


def prediction_readiness(db: Session, fixture) -> dict:
    from app.db.models import Fixture
    from app.services.model_registry import active_model_path

    reasons: list[str] = []
    failure_mode = None  # New: distinct failure mode
    checks = {
        "fixture_found": fixture is not None,
        "league_identified": bool(getattr(fixture, "league", None)),
        "teams_valid": False,
        "odds_present": False,
        "history_present": False,
        "model_available": False,  # New: check if sport model exists
    }
    if fixture is None:
        failure_mode = "fixture_not_found"
        log.warning("prediction_readiness: fixture_not_found")
        return {"ready": False, "reason": ["Fixture not found"], "failure_mode": failure_mode, "checks": checks}

    quality = validate_fixture(fixture.home_team, fixture.away_team, fixture.sport)
    checks["teams_valid"] = bool(quality.get("valid"))
    if not quality.get("valid"):
        failure_mode = "invalid_teams"
        reasons.append(f"Invalid fixture teams ({quality.get('reason', 'unknown')})")
        log.warning("prediction_readiness: invalid_teams for fixture %s - %s", getattr(fixture, "id", None), quality.get('reason'))

    has_odds = any(v is not None for v in (fixture.home_odds, fixture.draw_odds, fixture.away_odds))
    checks["odds_present"] = has_odds
    if not has_odds:
        if failure_mode is None:
            failure_mode = "no_odds"
        reasons.append("No odds market")
        log.info("prediction_readiness: no_odds for fixture %s", getattr(fixture, "id", None))

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
        if failure_mode is None:
            failure_mode = "insufficient_history"
        reasons.append("Insufficient team form / no completed history")
        log.info("prediction_readiness: insufficient_history for fixture %s (count=%d)", getattr(fixture, "id", None), history_count)

    # Model availability is only required for soccer (LoyalEdgeEngine).
    # All other sports use GenericSportEngine, which produces predictions
    # from raw form/odds features without a trained artefact.
    if sport == "soccer":
        try:
            model_path = active_model_path(db, sport)
            checks["model_available"] = model_path is not None
            if not model_path:
                if failure_mode is None:
                    failure_mode = "model_unavailable"
                reasons.append("No trained soccer model available")
                log.warning("prediction_readiness: model_unavailable for sport %s on fixture %s", sport, getattr(fixture, "id", None))
        except Exception:
            log.exception("model availability check failed for fixture %s sport %s", getattr(fixture, "id", None), sport)
            if failure_mode is None:
                failure_mode = "model_check_failed"
            reasons.append("Model availability check failed")
    else:
        checks["model_available"] = True  # GenericSportEngine needs no artefact

    # Future booking allowed when teams are real and we have either odds OR history.
    # Soccer additionally requires a trained model.
    ready = (
        checks["teams_valid"]
        and checks["league_identified"]
        and (checks["odds_present"] or checks["history_present"])
        and checks["model_available"]
    )
    if not checks["league_identified"]:
        if failure_mode is None:
            failure_mode = "league_not_identified"
        reasons.append("League not identified")
        log.info("prediction_readiness: league_not_identified for fixture %s", getattr(fixture, "id", None))

    result = {
        "ready": ready,
        "reason": reasons,
        "failure_mode": failure_mode,
        "checks": checks,
        "history_count": history_count,
    }

    if ready:
        log.info("prediction_readiness: READY for fixture %s (sport=%s, history=%d, odds=%s, model=%s)",
                 getattr(fixture, "id", None), sport, history_count, has_odds, checks.get("model_available"))
    else:
        log.info("prediction_readiness: NOT_READY for fixture %s (sport=%s, failure_mode=%s, reasons=%s)",
                 getattr(fixture, "id", None), sport, failure_mode, reasons)

    return result
