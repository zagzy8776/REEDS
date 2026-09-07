"""Closed-loop prediction feedback, live-match adaptation, and publication guard."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Fixture, MatchEvent, Prediction

DAILY_LOSS_LIMIT = 6
MAX_OPEN_PUBLIC_PICKS = 12
RECENT_WINDOW = 60
MIN_SEGMENT_SAMPLE = 8
LOSS_STREAK_CAUTION = 4


def prediction_result(prediction: Prediction, fixture: Fixture) -> bool | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    home, away = int(fixture.home_score), int(fixture.away_score)
    pick = str(prediction.pick or "").lower().strip()
    market = str(prediction.market or "").lower().strip()
    if market in {"1x2", "moneyline"}:
        if "home" in pick:
            return home > away
        if "away" in pick:
            return away > home
        if "draw" in pick:
            return home == away
    if market in {"goals", "over/under 1.5", "over/under 2.5", "over/under 3.5"}:
        total = home + away
        numbers = [part for part in pick.replace("goals", "").split() if part.replace(".", "", 1).isdigit()]
        threshold = float(numbers[-1]) if numbers else 2.5
        if "over" in pick:
            return total > threshold
        if "under" in pick:
            return total < threshold
    if market in {"btts", "both teams to score"}:
        scored = home > 0 and away > 0
        if "yes" in pick:
            return scored
        if "no" in pick:
            return not scored
    if market == "correct score":
        return pick == f"{home}-{away}"
    if market in {"total points", "total runs", "total games"}:
        total = home + away
        numbers = [part for part in pick.split() if part.replace(".", "", 1).isdigit()]
        threshold = float(numbers[-1]) if numbers else 2.5
        if pick.startswith("over"):
            return total > threshold
        if pick.startswith("under"):
            return total < threshold
    if market in {"spread", "point spread", "run line"}:
        numbers = [part for part in pick.replace("+", "").split() if part.replace(".", "", 1).isdigit()]
        spread = float(numbers[-1]) if numbers else 0.0
        if pick.startswith("home"):
            return (home - away) + spread > 0
        if pick.startswith("away"):
            return (away - home) + spread > 0
    if market == "double chance":
        if "home" in pick and "draw" in pick:
            return home >= away
        if "away" in pick and "draw" in pick:
            return away >= home
        if "home" in pick and "away" in pick:
            return home != away
    return None


def _confidence_bucket(confidence: float) -> str:
    if confidence < 55:
        return "<55"
    if confidence < 60:
        return "55-59"
    if confidence < 65:
        return "60-64"
    if confidence < 70:
        return "65-69"
    if confidence < 75:
        return "70-74"
    return "75+"


def _risk_upgrade(risk: str) -> str:
    return "Medium" if str(risk or "Medium").lower() == "low" else "High"


def settle_prediction_outcomes(db: Session, lookback_days: int = 90) -> dict[str, int]:
    cutoff = date.today() - timedelta(days=max(1, lookback_days))
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Fixture.match_date >= cutoff, Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
        .order_by(Fixture.match_date.desc(), Prediction.id.desc())
        .limit(12000)
        .all()
    )
    settled = won = lost = changed = 0
    for prediction, fixture in rows:
        result = prediction_result(prediction, fixture)
        if result is None:
            continue
        settled += 1
        won += int(result)
        lost += int(not result)
        meta = dict(prediction.engine_meta or {})
        old = meta.get("outcome") if isinstance(meta.get("outcome"), dict) else {}
        tags: list[str] = []
        if not result and float(prediction.confidence) >= 70:
            tags.append("high_confidence_loss")
        if not result and float(prediction.edge_score) >= 8:
            tags.append("high_edge_loss")
        if result and float(prediction.confidence) < 60:
            tags.append("low_confidence_win")
        outcome = {
            "result": "won" if result else "lost",
            "correct": bool(result),
            "settled_at": datetime.utcnow().isoformat(),
            "final_score": f"{fixture.home_score}-{fixture.away_score}",
            "confidence_at_pick": round(float(prediction.confidence), 3),
            "confidence_bucket": _confidence_bucket(float(prediction.confidence)),
            "learning_tags": tags,
        }
        comparable = {k: outcome[k] for k in ("result", "final_score", "confidence_at_pick", "learning_tags")}
        old_comparable = {k: old.get(k) for k in comparable}
        if comparable != old_comparable:
            meta["outcome"] = outcome
            prediction.engine_meta = meta
            changed += 1
    if changed:
        db.flush()
    return {"settled": settled, "won": won, "lost": lost, "updated": changed}


def _latest_public_rows(db: Session, days: int = RECENT_WINDOW) -> list[tuple[Prediction, Fixture, bool]]:
    cutoff = date.today() - timedelta(days=max(1, days))
    rows = (
        db.query(Prediction, Fixture)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Fixture.match_date >= cutoff, Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
        .order_by(Fixture.match_date.desc(), Prediction.version.desc(), Prediction.id.desc())
        .limit(12000)
        .all()
    )
    seen: set[tuple[int, str]] = set()
    result: list[tuple[Prediction, Fixture, bool]] = []
    for prediction, fixture in rows:
        key = (fixture.id, prediction.market)
        if key in seen:
            continue
        outcome = prediction_result(prediction, fixture)
        if outcome is None:
            continue
        seen.add(key)
        result.append((prediction, fixture, outcome))
    return result


def build_learning_context(db: Session, days: int = RECENT_WINDOW) -> dict[str, Any]:
    rows = _latest_public_rows(db, days)
    recent = rows[:30]
    daily_losses = sum(1 for _, fixture, correct in rows if fixture.match_date == date.today() and not correct)
    open_public = (
        db.query(Prediction.id)
        .join(Fixture, Prediction.fixture_id == Fixture.id)
        .filter(Prediction.is_published == True, Prediction.status == "active", Fixture.home_score.is_(None), Fixture.away_score.is_(None))
        .count()
    )
    wins = sum(1 for _, _, correct in recent if correct)
    losses = len(recent) - wins
    accuracy = (wins / len(recent) * 100.0) if recent else None
    ordered = sorted(rows, key=lambda row: (row[1].match_date, row[0].id), reverse=True)
    streak = 0
    for _, _, correct in ordered:
        if correct:
            break
        streak += 1

    segments: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "wins": 0, "confidence_sum": 0.0})
    buckets: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "wins": 0, "confidence_sum": 0.0})
    for prediction, fixture, correct in rows:
        confidence = float(prediction.confidence)
        for target in (segments[f"{fixture.sport}|{prediction.market}"], buckets[_confidence_bucket(confidence)]):
            target["n"] += 1
            target["wins"] += int(correct)
            target["confidence_sum"] += confidence

    def finalize(source: dict[str, dict[str, float | int]]) -> dict[str, dict[str, float | int]]:
        out: dict[str, dict[str, float | int]] = {}
        for key, value in source.items():
            n = int(value["n"])
            wins_count = int(value["wins"])
            avg_conf = float(value["confidence_sum"]) / n if n else 0.0
            out[key] = {"sample": n, "wins": wins_count, "losses": n - wins_count, "accuracy": round(wins_count / n * 100.0, 2) if n else None, "avg_confidence": round(avg_conf, 2)}
        return out

    segment_stats = finalize(segments)
    bucket_stats = finalize(buckets)
    pause_segments = {key for key, stats in segment_stats.items() if int(stats["sample"]) >= MIN_SEGMENT_SAMPLE and float(stats["accuracy"] or 0) < 40.0}
    if daily_losses >= DAILY_LOSS_LIMIT:
        guard = "pause"
    elif open_public >= MAX_OPEN_PUBLIC_PICKS:
        guard = "capacity"
    elif streak >= LOSS_STREAK_CAUTION or (len(recent) >= 12 and accuracy is not None and accuracy < 45.0):
        guard = "cautious"
    else:
        guard = "normal"
    return {
        "window_days": days,
        "settled_predictions": len(rows),
        "recent_sample": len(recent),
        "recent_wins": wins,
        "recent_losses": losses,
        "recent_accuracy": round(accuracy, 2) if accuracy is not None else None,
        "daily_losses": daily_losses,
        "current_loss_streak": streak,
        "daily_loss_limit": DAILY_LOSS_LIMIT,
        "max_open_public_picks": MAX_OPEN_PUBLIC_PICKS,
        "open_public_picks": open_public,
        "guard": guard,
        "paused_segments": sorted(pause_segments),
        "segments": segment_stats,
        "confidence_buckets": bucket_stats,
    }


def apply_learning_feedback(item: dict, fixture: Fixture, context: dict[str, Any]) -> dict:
    meta = dict(item.get("engine_meta") or {})
    confidence = float(item.get("confidence", 0.0))
    original_confidence = confidence
    segment_key = f"{fixture.sport}|{item.get('market', '')}"
    segment = (context.get("segments") or {}).get(segment_key)
    adjustment = 0.0
    if isinstance(segment, dict) and int(segment.get("sample", 0)) >= MIN_SEGMENT_SAMPLE:
        accuracy = float(segment.get("accuracy") or 0.0)
        expected = float(segment.get("avg_confidence") or confidence)
        gap = accuracy - expected
        if gap < -5.0:
            adjustment = -min(12.0, abs(gap) * 0.5)
            confidence = max(1.0, confidence + adjustment)
            item["edge_score"] = max(0.0, float(item.get("edge_score", 0.0)) + adjustment * 0.5)
            item["risk_level"] = _risk_upgrade(str(item.get("risk_level", "Medium")))
    if context.get("current_loss_streak", 0) >= LOSS_STREAK_CAUTION:
        adjustment -= 3.0
        confidence = max(1.0, confidence - 3.0)
        item["risk_level"] = _risk_upgrade(str(item.get("risk_level", "Medium")))
    item["confidence"] = round(confidence, 3)
    meta["learning_feedback"] = {
        "guard": context.get("guard", "normal"),
        "segment": segment_key,
        "segment_sample": int(segment.get("sample", 0)) if isinstance(segment, dict) else 0,
        "segment_accuracy": segment.get("accuracy") if isinstance(segment, dict) else None,
        "original_confidence": round(original_confidence, 3),
        "adjustment": round(adjustment, 3),
        "reason": "Recent settled outcomes are used as a calibration overlay; match results remain the training labels.",
    }
    item["engine_meta"] = meta
    return item


def apply_live_match_context(db: Session, item: dict, fixture: Fixture) -> dict:
    """React to live score/events so in-play reads do not blindly repeat pregame logic."""
    extra = fixture.extra if isinstance(fixture.extra, dict) else {}
    status = str(extra.get("status", "")).upper()
    live = bool(extra.get("live")) or status in {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}
    if not live or fixture.home_score is None or fixture.away_score is None:
        return item
    home, away = int(fixture.home_score), int(fixture.away_score)
    pick = str(item.get("pick", "")).lower()
    adjustment = 0.0
    if "home" in pick:
        adjustment += 7.0 if home > away else -7.0 if away > home else 0.0
    elif "away" in pick:
        adjustment += 7.0 if away > home else -7.0 if home > away else 0.0
    elif "draw" in pick:
        adjustment += 4.0 if home == away else -4.0
    if fixture.sport == "soccer":
        events = db.query(MatchEvent).filter(MatchEvent.fixture_id == fixture.id).all()
        home_red = sum(1 for event in events if event.event_type == "red_card" and event.team == fixture.home_team)
        away_red = sum(1 for event in events if event.event_type == "red_card" and event.team == fixture.away_team)
        if "home" in pick:
            adjustment += -5.0 if home_red > away_red else 5.0 if away_red > home_red else 0.0
        elif "away" in pick:
            adjustment += -5.0 if away_red > home_red else 5.0 if home_red > away_red else 0.0
    item["confidence"] = round(max(1.0, min(99.0, float(item.get("confidence", 0.0)) + adjustment)), 3)
    meta = dict(item.get("engine_meta") or {})
    meta["live_context"] = {"status": status or "LIVE", "score": f"{home}-{away}", "adjustment": round(adjustment, 2), "reason": "Live score and match events update the pregame read during play."}
    item["engine_meta"] = meta
    return item


def publication_allowed(item: dict, fixture: Fixture, context: dict[str, Any]) -> tuple[bool, str | None]:
    if context.get("guard") == "pause":
        return False, "daily loss circuit-breaker reached"
    if context.get("guard") == "capacity":
        return False, "public-pick capacity is full; waiting for settled games"
    segment_key = f"{fixture.sport}|{item.get('market', '')}"
    if segment_key in set(context.get("paused_segments") or []):
        return False, "sport/market segment is underperforming in recent settled picks"
    return True, None


def learning_summary(db: Session) -> dict[str, Any]:
    context = build_learning_context(db)
    recent_rows = _latest_public_rows(db, 30)[:20]
    losses = []
    for prediction, fixture, correct in recent_rows:
        if correct:
            continue
        meta = prediction.engine_meta if isinstance(prediction.engine_meta, dict) else {}
        losses.append({
            "prediction_id": prediction.id,
            "fixture_id": fixture.id,
            "sport": fixture.sport,
            "league": fixture.league,
            "match": f"{fixture.home_team} vs {fixture.away_team}",
            "market": prediction.market,
            "pick": prediction.pick,
            "confidence": prediction.confidence,
            "final_score": f"{fixture.home_score}-{fixture.away_score}",
            "learning_tags": ((meta.get("outcome") or {}).get("learning_tags") if isinstance(meta.get("outcome"), dict) else []),
            "reasoning": prediction.reasoning,
        })
    return {**context, "recent_losses": losses[:10]}
