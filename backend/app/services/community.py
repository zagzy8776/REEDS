from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Fixture, UserPrediction


def grade_user_prediction(pred: UserPrediction, fixture: Fixture) -> bool | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    home_score, away_score = fixture.home_score, fixture.away_score
    market = pred.market.lower()
    pick = pred.pick.lower()
    if market in {"1x2", "moneyline"}:
        if "home" in pick:
            return home_score > away_score
        if "away" in pick:
            return away_score > home_score
        if "draw" in pick:
            return home_score == away_score
    if market == "goals":
        total = home_score + away_score
        if "over" in pick:
            return total > 2.5
        if "under" in pick:
            return total < 2.5
    if market == "btts":
        both = home_score > 0 and away_score > 0
        if "yes" in pick:
            return both
        if "no" in pick:
            return not both
    return None


def selected_fixture_odds(pred: UserPrediction, fixture: Fixture) -> float | None:
    pick = pred.pick.lower()
    if "home" in pick:
        return fixture.home_odds
    if "away" in pick:
        return fixture.away_odds
    if "draw" in pick:
        return fixture.draw_odds
    return None


def settle_user_predictions(db: Session) -> dict:
    rows = db.query(UserPrediction, Fixture).join(Fixture, UserPrediction.fixture_id == Fixture.id).filter(UserPrediction.is_settled == False, Fixture.home_score != None, Fixture.away_score != None).all()
    settled = 0
    for pred, fixture in rows:
        result = grade_user_prediction(pred, fixture)
        if result is None:
            continue
        odds = selected_fixture_odds(pred, fixture)
        pred.is_settled = True
        pred.was_correct = result
        pred.profit_units = ((odds - 1) * pred.stake_units) if result and odds else pred.stake_units if result else -pred.stake_units
        pred.settled_at = datetime.utcnow()
        settled += 1
    db.commit()
    return {"settled": settled}


def community_leaderboard(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(UserPrediction).filter(UserPrediction.is_settled == True).order_by(UserPrediction.created_at.asc()).all()
    users: dict[str, dict] = {}
    for pred in rows:
        row = users.setdefault(pred.username, {"username": pred.username, "settled": 0, "wins": 0, "profit_units": 0.0, "current_streak": 0, "best_streak": 0})
        row["settled"] += 1
        if pred.was_correct:
            row["wins"] += 1
            row["current_streak"] += 1
            row["best_streak"] = max(row["best_streak"], row["current_streak"])
        else:
            row["current_streak"] = 0
        row["profit_units"] += pred.profit_units or 0.0
    out = []
    for row in users.values():
        row["win_rate"] = round((row["wins"] / row["settled"]) * 100, 1) if row["settled"] else 0
        row["profit_units"] = round(row["profit_units"], 2)
        out.append(row)
    return sorted(out, key=lambda x: (x["profit_units"], x["win_rate"], x["settled"]), reverse=True)[:limit]


def fixture_consensus(db: Session, fixture_id: int) -> dict:
    rows = db.query(UserPrediction).filter(UserPrediction.fixture_id == fixture_id).order_by(UserPrediction.created_at.desc()).all()
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row.market}: {row.pick}"
        counts[key] = counts.get(key, 0) + 1
    total = len(rows)
    return {"total": total, "consensus": [{"pick": k, "count": v, "percent": round((v / total) * 100, 1) if total else 0} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)], "entries": [{"id": p.id, "username": p.username, "market": p.market, "pick": p.pick, "analysis_text": p.analysis_text, "created_at": p.created_at, "is_settled": p.is_settled, "was_correct": p.was_correct} for p in rows]}