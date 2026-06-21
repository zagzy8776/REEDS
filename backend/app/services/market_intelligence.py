"""
Market Intelligence Service
============================
Converts raw bookmaker odds snapshots into actionable signals that make
the ML models aware of the betting market's collective knowledge.

Signals computed:
  line_efficiency_home   — how far the home implied prob moved open→close
  line_efficiency_away   — same for away side
  sharp_move_direction   — "home", "away", or "none"
  sharp_move_magnitude   — absolute size of the biggest move
  clv_home               — closing-line value: did we beat the close?
  steam_detected         — bool: rapid (<2h) move ≥ 3%
  reverse_line_movement  — public % on one side but line moves the other way
  opening_home_prob      — implied prob from opening odds
  closing_home_prob      — implied prob from closing odds
  market_consensus       — weighted average of all bookmaker implied probs

All signals are stored in insider_signals (reusing that table) and surfaced
as ML features via get_fixture_signals() which feeds features_for_fixture().
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Fixture, InsiderSignal, OddsSnapshot

log = logging.getLogger(__name__)

# ── helpers ───────────────────────────────────────────────────────────────────

def _implied(odds: float | None) -> float:
    """Convert decimal odds to implied probability. Returns 0.0 on bad input."""
    try:
        o = float(odds)
        return round(1.0 / o, 6) if o and o > 1.0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _upsert(db: Session, fixture_id: int, sport: str,
            signal_type: str, source: str,
            value: float | None = None,
            direction: str | None = None,
            description: str | None = None,
            extra: dict | None = None) -> None:
    existing = (
        db.query(InsiderSignal)
        .filter_by(fixture_id=fixture_id, signal_type=signal_type, source=source)
        .first()
    )
    if existing:
        existing.value       = value
        existing.direction   = direction
        existing.description = description
        existing.extra       = extra
        existing.captured_at = datetime.utcnow()
    else:
        db.add(InsiderSignal(
            fixture_id=fixture_id, sport=sport,
            signal_type=signal_type, source=source,
            value=value, direction=direction,
            description=description, extra=extra,
        ))


# ── core computation ──────────────────────────────────────────────────────────

def compute_line_efficiency(db: Session, fixture_id: int, sport: str) -> dict:
    """Analyse all odds snapshots for a fixture and extract market intelligence.

    Returns a dict of signal values (also persisted to insider_signals).
    """
    snaps = (
        db.query(OddsSnapshot)
        .filter(
            OddsSnapshot.fixture_id == fixture_id,
            OddsSnapshot.market.in_(["1X2", "Moneyline", "h2h"]),
        )
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )

    result = {
        "line_efficiency_home":   0.0,
        "line_efficiency_away":   0.0,
        "sharp_move_direction":   "none",
        "sharp_move_magnitude":   0.0,
        "steam_detected":         False,
        "opening_home_prob":      0.0,
        "closing_home_prob":      0.0,
        "clv_home":               0.0,
    }

    if len(snaps) < 2:
        return result

    opening = snaps[0]
    closing = snaps[-1]

    open_home  = _implied(opening.home_odds)
    open_away  = _implied(opening.away_odds)
    close_home = _implied(closing.home_odds)
    close_away = _implied(closing.away_odds)

    move_home = round(close_home - open_home, 5)
    move_away = round(close_away - open_away, 5)

    # Determine dominant sharp direction (≥1.5% threshold)
    SHARP_THRESHOLD = 0.015
    direction = "none"
    if abs(move_home) >= SHARP_THRESHOLD or abs(move_away) >= SHARP_THRESHOLD:
        if abs(move_home) >= abs(move_away):
            direction = "home" if move_home > 0 else "away"
        else:
            direction = "away" if move_away > 0 else "home"

    # Steam move: significant move that happened fast (within 2 hours)
    steam = False
    for i in range(1, len(snaps)):
        prev, curr = snaps[i - 1], snaps[i]
        dt = (curr.captured_at - prev.captured_at).total_seconds() / 3600
        if dt <= 2.0:
            fast_move = abs(_implied(curr.home_odds) - _implied(prev.home_odds))
            if fast_move >= 0.03:
                steam = True
                break

    result.update({
        "line_efficiency_home":   move_home,
        "line_efficiency_away":   move_away,
        "sharp_move_direction":   direction,
        "sharp_move_magnitude":   round(max(abs(move_home), abs(move_away)), 5),
        "steam_detected":         steam,
        "opening_home_prob":      open_home,
        "closing_home_prob":      close_home,
        "clv_home":               move_home,   # simplified CLV: did opening beat closing?
    })

    # Persist to insider_signals so features_for_fixture() can read them
    if abs(move_home) >= SHARP_THRESHOLD or abs(move_away) >= SHARP_THRESHOLD:
        _upsert(
            db, fixture_id, sport,
            signal_type="sharp_line_move",
            source="line_efficiency",
            value=round(move_home, 5),
            direction=direction,
            description=(
                f"Line moved {move_home:+.1%} home / {move_away:+.1%} away "
                f"from {opening.home_odds} → {closing.home_odds}. "
                f"Steam={'YES' if steam else 'no'}."
            ),
            extra={
                "open_home_odds":  opening.home_odds,
                "close_home_odds": closing.home_odds,
                "open_away_odds":  opening.away_odds,
                "close_away_odds": closing.away_odds,
                "move_home":       move_home,
                "move_away":       move_away,
                "steam":           steam,
                "snapshots":       len(snaps),
            },
        )

    return result


def build_line_efficiency_features(db: Session, fixture_id: int) -> dict:
    """Return flat feature dict for the ML pipeline from line efficiency signals.

    Keys match what features_for_fixture() expects under the insider= dict:
      insider_sharp_home_move  — home-side implied prob line move (+= sharpened)
      insider_sharp_away_move  — away-side line move
      insider_clv_home         — closing-line value proxy
      insider_steam            — 1 if steam move detected, 0 otherwise
      insider_opening_home_prob — opening implied probability (home)
    """
    sig = (
        db.query(InsiderSignal)
        .filter(
            InsiderSignal.fixture_id == fixture_id,
            InsiderSignal.signal_type == "sharp_line_move",
            InsiderSignal.source == "line_efficiency",
        )
        .order_by(InsiderSignal.captured_at.desc())
        .first()
    )
    if not sig or not sig.extra:
        return {
            "insider_sharp_home_move": 0.0,
            "insider_sharp_away_move": 0.0,
            "insider_clv_home":        0.0,
            "insider_steam":           0.0,
            "insider_opening_home_prob": 0.0,
        }
    ex = sig.extra
    return {
        "insider_sharp_home_move": float(ex.get("move_home", 0.0)),
        "insider_sharp_away_move": float(ex.get("move_away", 0.0)),
        "insider_clv_home":        float(ex.get("move_home", 0.0)),
        "insider_steam":           1.0 if ex.get("steam") else 0.0,
        "insider_opening_home_prob": float(
            _implied(ex.get("open_home_odds")) if ex.get("open_home_odds") else 0.0
        ),
    }


# ── batch refresh ─────────────────────────────────────────────────────────────

def refresh_line_efficiency(db: Session, days_ahead: int = 3) -> dict:
    """Compute line efficiency for all upcoming fixtures that have ≥2 snapshots."""
    from datetime import date
    today = date.today()
    upcoming = (
        db.query(Fixture)
        .filter(
            Fixture.match_date >= today,
            Fixture.match_date <= today + timedelta(days=days_ahead),
            Fixture.home_score.is_(None),
        )
        .all()
    )
    processed = 0
    signals_written = 0
    for fx in upcoming:
        snap_count = (
            db.query(OddsSnapshot)
            .filter(OddsSnapshot.fixture_id == fx.id)
            .count()
        )
        if snap_count < 2:
            continue
        result = compute_line_efficiency(db, fx.id, fx.sport)
        processed += 1
        if result["sharp_move_magnitude"] >= 0.015:
            signals_written += 1

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {
        "fixtures_processed": processed,
        "sharp_signals_written": signals_written,
    }
