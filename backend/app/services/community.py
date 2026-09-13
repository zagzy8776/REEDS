from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import CommunityComment, CommunityPlay, CommunityReaction, Fixture, UserPrediction, WinSlip


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
    rows = db.query(UserPrediction).order_by(UserPrediction.created_at.asc()).all()
    users: dict[str, dict] = {}
    for pred in rows:
        row = users.setdefault(pred.username, {
            "username": pred.username,
            "total_posts": 0,
            "pending": 0,
            "settled": 0,
            "wins": 0,
            "losses": 0,
            "profit_units": 0.0,
            "current_streak": 0,
            "best_streak": 0,
            "markets": {},
            "recent_picks": [],
            "last_active": None,
        })
        row["total_posts"] += 1
        row["last_active"] = max(row["last_active"], pred.created_at) if row["last_active"] else pred.created_at
        row["markets"][pred.market] = row["markets"].get(pred.market, 0) + 1
        row["recent_picks"].append({
            "id": pred.id,
            "fixture_id": pred.fixture_id,
            "market": pred.market,
            "pick": pred.pick,
            "is_settled": pred.is_settled,
            "was_correct": pred.was_correct,
            "created_at": pred.created_at,
        })
        if not pred.is_settled:
            row["pending"] += 1
            continue
        row["settled"] += 1
        if pred.was_correct:
            row["wins"] += 1
            row["current_streak"] += 1
            row["best_streak"] = max(row["best_streak"], row["current_streak"])
        else:
            row["losses"] += 1
            row["current_streak"] = 0
        row["profit_units"] += pred.profit_units or 0.0
    out = []
    for row in users.values():
        row["win_rate"] = round((row["wins"] / row["settled"]) * 100, 1) if row["settled"] else 0
        row["profit_units"] = round(row["profit_units"], 2)
        row["roi_percent"] = round((row["profit_units"] / (row["settled"] * 10)) * 100, 1) if row["settled"] else 0
        row["favorite_market"] = max(row["markets"].items(), key=lambda item: item[1])[0] if row["markets"] else "-"
        row["recent_picks"] = sorted(row["recent_picks"], key=lambda x: x["created_at"], reverse=True)[:5]
        row["badges"] = []
        row["level"] = "Rookie"
        row["level_color"] = "slate"
        if row["settled"] >= 25:
            row["badges"].append("Verified volume")
        if row["win_rate"] >= 60 and row["settled"] >= 10:
            row["badges"].append("Sharp form")
        if row["best_streak"] >= 5:
            row["badges"].append("Hot streak")
        if row["profit_units"] > 0:
            row["badges"].append("Profitable")
        if row["win_rate"] >= 70 and row["settled"] >= 15:
            row["level"] = "Legend"
            row["level_color"] = "amber"
        elif row["win_rate"] >= 60 and row["settled"] >= 10:
            row["level"] = "Elite winner"
            row["level_color"] = "emerald"
        elif row["wins"] >= 5 or row["best_streak"] >= 3:
            row["level"] = "Hot tipster"
            row["level_color"] = "sky"
        elif row["total_posts"] >= 3:
            row["level"] = "Active voice"
            row["level_color"] = "violet"
        row["rank_score"] = round(row["profit_units"] + (row["win_rate"] / 10) + min(row["settled"], 50) / 5 + row["best_streak"], 2)
        row.pop("markets", None)
        out.append(row)
    return sorted(out, key=lambda x: (x["rank_score"], x["profit_units"], x["win_rate"], x["settled"]), reverse=True)[:limit]


def _post_social_summary(db: Session, post_ids: list[int]) -> dict[int, dict]:
    if not post_ids:
        return {}
    out = {post_id: {"comments": 0, "plays": 0, "win_slips": 0, "rating_count": 0, "average_rating": 0, "reactions": {}} for post_id in post_ids}
    comment_rows = db.query(CommunityComment.prediction_id, func.count(CommunityComment.id)).filter(CommunityComment.prediction_id.in_(post_ids)).group_by(CommunityComment.prediction_id).all()
    play_rows = db.query(CommunityPlay.prediction_id, func.count(CommunityPlay.id)).filter(CommunityPlay.prediction_id.in_(post_ids)).group_by(CommunityPlay.prediction_id).all()
    win_rows = db.query(WinSlip.prediction_id, func.count(WinSlip.id)).filter(WinSlip.prediction_id.in_(post_ids)).group_by(WinSlip.prediction_id).all()
    reaction_rows = db.query(CommunityReaction.prediction_id, CommunityReaction.reaction, func.count(CommunityReaction.id), func.avg(CommunityReaction.rating), func.count(CommunityReaction.rating)).filter(CommunityReaction.prediction_id.in_(post_ids)).group_by(CommunityReaction.prediction_id, CommunityReaction.reaction).all()
    for post_id, count in comment_rows:
        out[post_id]["comments"] = count
    for post_id, count in play_rows:
        out[post_id]["plays"] = count
    for post_id, count in win_rows:
        out[post_id]["win_slips"] = count
    for post_id, reaction, count, avg_rating, rating_count in reaction_rows:
        out[post_id]["reactions"][reaction] = count
        if rating_count:
            out[post_id]["average_rating"] = round(float(avg_rating or 0), 1)
            out[post_id]["rating_count"] += int(rating_count)
    return out


def weekly_winners(db: Session, limit: int = 8) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=7)
    rows = db.query(UserPrediction).filter(UserPrediction.created_at >= since).order_by(UserPrediction.created_at.asc()).all()
    users: dict[str, dict] = {}
    for pred in rows:
        row = users.setdefault(pred.username, {"username": pred.username, "posts": 0, "settled": 0, "wins": 0, "profit_units": 0.0, "win_rate": 0, "weekly_score": 0})
        row["posts"] += 1
        if pred.is_settled:
            row["settled"] += 1
            if pred.was_correct:
                row["wins"] += 1
            row["profit_units"] += pred.profit_units or 0.0
    out = []
    for row in users.values():
        row["win_rate"] = round((row["wins"] / row["settled"]) * 100, 1) if row["settled"] else 0
        row["profit_units"] = round(row["profit_units"], 2)
        row["weekly_score"] = round((row["wins"] * 3) + row["profit_units"] + min(row["posts"], 10) + (row["win_rate"] / 20), 2)
        row["level"] = "Weekly contender"
        if row["wins"] >= 5 and row["win_rate"] >= 60:
            row["level"] = "Winner of the week"
        elif row["wins"] >= 3:
            row["level"] = "Hot this week"
        out.append(row)
    return sorted(out, key=lambda x: (x["weekly_score"], x["wins"], x["profit_units"]), reverse=True)[:limit]


def community_overview(db: Session) -> dict:
    rows = db.query(UserPrediction).order_by(UserPrediction.created_at.desc()).all()
    settled = [p for p in rows if p.is_settled]
    wins = sum(1 for p in settled if p.was_correct)
    since = datetime.utcnow() - timedelta(days=7)
    active_users = len({p.username for p in rows if p.created_at and p.created_at >= since})
    markets: dict[str, int] = {}
    for p in rows:
        markets[p.market] = markets.get(p.market, 0) + 1
    comments = db.query(CommunityComment).count()
    plays = db.query(CommunityPlay).count()
    reactions = db.query(CommunityReaction).count()
    win_slips = db.query(WinSlip).order_by(WinSlip.created_at.desc()).limit(10).all()
    recent_posts = rows[:12]
    social = _post_social_summary(db, [p.id for p in recent_posts])
    return {
        "total_posts": len(rows),
        "pending": sum(1 for p in rows if not p.is_settled),
        "settled": len(settled),
        "wins": wins,
        "losses": len(settled) - wins,
        "community_hit_rate": round((wins / len(settled)) * 100, 1) if settled else 0,
        "active_users_7d": active_users,
        "comments": comments,
        "plays": plays,
        "ratings": reactions,
        "weekly_winners": weekly_winners(db),
        "top_markets": [{"market": k, "count": v} for k, v in sorted(markets.items(), key=lambda x: x[1], reverse=True)[:6]],
        "recent_posts": [{"id": p.id, "fixture_id": p.fixture_id, "username": p.username, "market": p.market, "pick": p.pick, "analysis_text": p.analysis_text, "is_settled": p.is_settled, "was_correct": p.was_correct, "profit_units": p.profit_units, "created_at": p.created_at, "social": social.get(p.id, {})} for p in recent_posts],
        "recent_wins": [{"id": w.id, "prediction_id": w.prediction_id, "username": w.username, "title": w.title, "proof_text": w.proof_text, "profit_units": w.profit_units, "created_at": w.created_at} for w in win_slips],
    }


def fixture_consensus(db: Session, fixture_id: int) -> dict:
    rows = db.query(UserPrediction).filter(UserPrediction.fixture_id == fixture_id).order_by(UserPrediction.created_at.desc()).all()
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row.market}: {row.pick}"
        counts[key] = counts.get(key, 0) + 1
    total = len(rows)
    return {"total": total, "consensus": [{"pick": k, "count": v, "percent": round((v / total) * 100, 1) if total else 0} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)], "entries": [{"id": p.id, "username": p.username, "market": p.market, "pick": p.pick, "analysis_text": p.analysis_text, "created_at": p.created_at, "is_settled": p.is_settled, "was_correct": p.was_correct} for p in rows]}


def experts_list(db: Session, limit: int = 50) -> list[dict]:
    """Return users with >60% win rate and at least 10 settled picks."""
    rows = db.query(UserPrediction).all()
    users: dict[str, dict] = {}
    for pred in rows:
        row = users.setdefault(pred.username, {"username": pred.username, "settled": 0, "wins": 0, "losses": 0, "profit_units": 0.0, "current_streak": 0, "best_streak": 0, "markets": {}})
        row["settled"] += 1
        if pred.is_settled:
            if pred.was_correct:
                row["wins"] += 1
                row["current_streak"] += 1
                row["best_streak"] = max(row["best_streak"], row["current_streak"])
            else:
                row["losses"] += 1
                row["current_streak"] = 0
            row["profit_units"] += pred.profit_units or 0.0
    out = []
    for row in users.values():
        if row["settled"] < 10:
            continue
        row["win_rate"] = round((row["wins"] / row["settled"]) * 100, 1)
        row["profit_units"] = round(row["profit_units"], 2)
        if row["win_rate"] <= 60:
            continue
        row["roi_percent"] = round((row["profit_units"] / (row["settled"] * 10)) * 100, 1)
        row["level"] = "Legend" if row["win_rate"] >= 70 and row["settled"] >= 15 else "Elite winner" if row["win_rate"] >= 65 else "Sharp tipster"
        out.append(row)
    return sorted(out, key=lambda x: (x["win_rate"], x["settled"], x["profit_units"]), reverse=True)[:limit]


def win_wall(db: Session, limit: int = 30) -> list[dict]:
    """Return winning slips sorted by profit, most recent first."""
    slips = db.query(WinSlip).filter(WinSlip.profit_units != None, WinSlip.profit_units > 0).order_by(WinSlip.created_at.desc()).limit(limit).all()
    return [{"id": w.id, "prediction_id": w.prediction_id, "username": w.username, "title": w.title, "proof_text": w.proof_text, "profit_units": w.profit_units, "created_at": w.created_at} for w in slips]


def daily_challenge(db: Session) -> dict:
    """Pick the biggest upcoming match for today's community challenge."""
    from datetime import date, timedelta
    today = date.today()
    tomorrow = today + timedelta(days=1)
    fixture = db.query(Fixture).filter(Fixture.match_date >= today, Fixture.match_date < tomorrow, Fixture.sport == "soccer").order_by(Fixture.match_date.asc()).first()
    if not fixture:
        fixture = db.query(Fixture).filter(Fixture.match_date >= today, Fixture.match_date <= tomorrow, Fixture.sport == "soccer").order_by(Fixture.match_date.asc()).first()
    if not fixture:
        fixture = db.query(Fixture).filter(Fixture.match_date >= today).order_by(Fixture.match_date.asc()).first()
    if not fixture:
        return {"active": False, "message": "No fixtures for today's challenge"}
    community_picks = fixture_consensus(db, fixture.id)
    return {
        "active": True,
        "fixture": {"id": fixture.id, "home_team": fixture.home_team, "away_team": fixture.away_team, "match_date": fixture.match_date, "league": fixture.league},
        "community": community_picks,
    }


def follow_user(db: Session, follower: str, following: str) -> dict:
    """Simple follow/unfollow toggle. Uses CommunityPlay as a lightweight follow store."""
    existing = db.query(CommunityPlay).filter(CommunityPlay.username == follower, CommunityPlay.prediction_id == hash(following) % 1000000).first()
    if existing:
        db.delete(existing)
        db.commit()
        return {"status": "unfollowed", "following": following}
    row = CommunityPlay(username=follower, prediction_id=hash(following) % 1000000, stake_units=0)
    db.add(row)
    db.commit()
    return {"status": "followed", "following": following}


def user_profile(db: Session, username: str) -> dict | None:
    """Return a user's full prediction history and stats."""
    rows = db.query(UserPrediction).filter(UserPrediction.username == username).order_by(UserPrediction.created_at.desc()).all()
    if not rows:
        return None
    settled = [p for p in rows if p.is_settled]
    wins = sum(1 for p in settled if p.was_correct)
    losses = len(settled) - wins
    win_rate = round((wins / len(settled)) * 100, 1) if settled else 0
    profit = round(sum(p.profit_units or 0 for p in settled), 2)
    current_streak = 0
    best_streak = 0
    for p in reversed(settled):
        if p.was_correct:
            current_streak += 1
            best_streak = max(best_streak, current_streak)
        else:
            current_streak = 0
    level = "Rookie"
    badges = []
    if settled:
        if win_rate >= 70 and len(settled) >= 15:
            level = "Legend"
            badges.append("🏆 Legend")
        elif win_rate >= 60 and len(settled) >= 10:
            level = "Elite winner"
            badges.append("⭐ Elite winner")
        elif wins >= 5:
            level = "Hot tipster"
            badges.append("🔥 Hot tipster")
        if best_streak >= 5:
            badges.append("🔥 Hot streak")
        if profit > 0:
            badges.append("💰 Profitable")
    picks_history = []
    for p in rows[:20]:
        picks_history.append({"id": p.id, "fixture_id": p.fixture_id, "market": p.market, "pick": p.pick, "is_settled": p.is_settled, "was_correct": p.was_correct, "profit_units": p.profit_units, "created_at": p.created_at})
    return {
        "username": username,
        "level": level,
        "badges": badges,
        "total_posts": len(rows),
        "settled": len(settled),
        "pending": len(rows) - len(settled),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_units": profit,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "recent_picks": picks_history,
    }


def prediction_social_context(db: Session, prediction_id: int) -> dict:
    comments = db.query(CommunityComment).filter(CommunityComment.prediction_id == prediction_id).order_by(CommunityComment.created_at.desc()).limit(50).all()
    reactions = db.query(CommunityReaction).filter(CommunityReaction.prediction_id == prediction_id).all()
    plays = db.query(CommunityPlay).filter(CommunityPlay.prediction_id == prediction_id).order_by(CommunityPlay.created_at.desc()).limit(50).all()
    wins = db.query(WinSlip).filter(WinSlip.prediction_id == prediction_id).order_by(WinSlip.created_at.desc()).limit(20).all()
    reaction_counts: dict[str, int] = {}
    ratings = []
    for r in reactions:
        reaction_counts[r.reaction] = reaction_counts.get(r.reaction, 0) + 1
        if r.rating:
            ratings.append(r.rating)
    return {
        "comments": [{"id": c.id, "username": c.username, "comment_text": c.comment_text, "created_at": c.created_at} for c in comments],
        "reactions": reaction_counts,
        "average_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
        "rating_count": len(ratings),
        "plays": [{"id": p.id, "username": p.username, "stake_units": p.stake_units, "status": p.status, "created_at": p.created_at} for p in plays],
        "plays_count": len(plays),
        "win_slips": [{"id": w.id, "username": w.username, "title": w.title, "proof_text": w.proof_text, "profit_units": w.profit_units, "created_at": w.created_at} for w in wins],
    }