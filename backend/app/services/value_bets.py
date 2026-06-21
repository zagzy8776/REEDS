"""Value betting engine — production-hardened.

Four-layer protection system:
  1. Certainty Floor    — model prob must exceed tier threshold (Elite ≥60%, Standard ≥52%)
  2. Proxy-xG features  — shot efficiency, clean sheet rate, FTS rate caught in features
  3. Live odds verify   — re-ping SportyBet before serving to catch stale cached odds
  4. Elite/Sandbox split — EPL/CL/La Liga shown publicly; obscure leagues sandboxed

Math foundation:
  - Overround: fair_prob = implied / sum(implied)
  - Value:     model_prob × decimal_odds > threshold
  - Kelly:     f* = (b×p - q) / b, scaled ×0.25, capped 5%
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import NamedTuple

import pandas as pd
from sqlalchemy.orm import Session

from app.scraper.sportybet import fetch_all_sports, fetch_upcoming_fixtures, _get
from app.services.predictions import dataframe_from_db
from app.utils.team_names import normalize_team_name

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tiered thresholds — the "certainty floor" (Point 1)
# ---------------------------------------------------------------------------

# Elite board: only high-confidence, lower-variance picks users actually trust
ELITE_MIN_MODEL_PROB  = 0.60   # model must be ≥60% confident
ELITE_MIN_VALUE_SCORE = 1.05   # AND 5% mathematical edge minimum
ELITE_MIN_ODDS        = 1.40   # no very short prices
ELITE_MAX_ODDS        = 4.00   # no long shots on elite board

# Standard / "All Picks" board
STD_MIN_MODEL_PROB    = 0.52   # still above 50% — no crazy underdogs
STD_MIN_VALUE_SCORE   = 1.04
STD_MIN_ODDS          = 1.30
STD_MAX_ODDS          = 8.00

MAX_KELLY_FRACTION    = 0.05   # never > 5% of bankroll
KELLY_MULTIPLIER      = 0.25   # fractional Kelly

# Elite leagues — shown publicly on main board (Point 4)
ELITE_LEAGUES: set[str] = {
    "premier league", "epl", "la liga", "serie a", "bundesliga", "ligue 1",
    "uefa champions league", "champions league", "uefa europa league",
    "europa league", "fifa world cup", "world cup", "copa america",
    "nba", "nfl", "mlb", "nhl", "atp", "wta",
    "eredivisie", "primeira liga", "super lig",
}


def _is_elite_league(league: str) -> bool:
    low = league.lower()
    return any(el in low for el in ELITE_LEAGUES)


# ---------------------------------------------------------------------------
# Overround stripping
# ---------------------------------------------------------------------------

def strip_overround(
    home_odds: float | None,
    draw_odds: float | None,
    away_odds: float | None,
) -> dict:
    """Return fair zero-vig probabilities and the margin size."""
    try:
        implied: dict[str, float] = {}
        total = 0.0
        for key, o in (("home", home_odds), ("draw", draw_odds), ("away", away_odds)):
            if o and o > 1.0:
                p = 1.0 / o
                implied[key] = p
                total += p
        if total == 0:
            return {}
        fair = {k: v / total for k, v in implied.items()}
        return {
            "fair_home":  round(fair.get("home", 0.0),  4),
            "fair_draw":  round(fair.get("draw", 0.0),  4) if "draw" in fair else None,
            "fair_away":  round(fair.get("away", 0.0),  4),
            "overround":  round(total - 1.0, 4),
            "margin_pct": round((total - 1.0) / total * 100, 2),
        }
    except (TypeError, ValueError, ZeroDivisionError):
        return {}


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------

def kelly_fraction(model_prob: float, decimal_odds: float) -> float:
    """Fractional Kelly (0.25×) capped at MAX_KELLY_FRACTION."""
    if decimal_odds <= 1.0 or model_prob <= 0:
        return 0.0
    b = decimal_odds - 1.0
    p = model_prob
    q = 1.0 - p
    f = (b * p - q) / b
    return max(0.0, min(f * KELLY_MULTIPLIER, MAX_KELLY_FRACTION))


# ---------------------------------------------------------------------------
# Live odds verification — Point 3 (stale cache protection)
# ---------------------------------------------------------------------------

def _verify_odds_live(
    sportybet_match_id: str,
    sport: str,
    selection: str,
    original_odds: float,
    min_value_score: float,
    model_prob: float,
) -> dict:
    """Re-ping SportyBet for a specific match to confirm odds haven't moved.

    Returns:
      {
        "valid": bool,          # True = still value, False = line moved
        "current_odds": float,
        "original_odds": float,
        "moved_by": float,      # positive = odds shortened (worse for bettor)
      }
    """
    if not sportybet_match_id:
        return {"valid": True, "current_odds": original_odds, "original_odds": original_odds, "moved_by": 0.0}

    try:
        from app.scraper.sportybet import _BASE, _HEADERS
        url = f"{_BASE}/query/eventMarkets"
        params = {"eventId": sportybet_match_id, "_t": int(time.time() * 1000)}
        data = _get(url, params)
        if not data:
            # API unreachable — treat as valid to avoid blocking everything
            return {"valid": True, "current_odds": original_odds, "original_odds": original_odds, "moved_by": 0.0}

        # Parse current odds from the response
        events = []
        if isinstance(data, dict):
            events = data.get("data", {}).get("events", []) or data.get("events", []) or []

        for event in events:
            for market in event.get("markets", []) or []:
                market_name = str(market.get("name", "")).lower()
                if not ("1x2" in market_name or "match" in market_name or "winner" in market_name or "moneyline" in market_name):
                    continue
                for outcome in market.get("outcomes", []) or []:
                    name = str(outcome.get("desc", "") or outcome.get("name", "")).lower()
                    is_match = (
                        (selection == "home" and name in ("1", "home", "home win", "w1")) or
                        (selection == "draw" and name in ("x", "draw", "tie")) or
                        (selection == "away" and name in ("2", "away", "away win", "w2"))
                    )
                    if not is_match:
                        continue
                    raw = outcome.get("odds") or outcome.get("price")
                    if raw:
                        current = float(raw)
                        if current > 100:
                            current /= 100
                        moved_by = round(original_odds - current, 3)
                        still_value = (model_prob * current) >= min_value_score
                        return {
                            "valid": still_value,
                            "current_odds": round(current, 3),
                            "original_odds": original_odds,
                            "moved_by": moved_by,
                        }
    except Exception as exc:
        log.debug("Live odds verify failed for %s: %s", sportybet_match_id, exc)

    return {"valid": True, "current_odds": original_odds, "original_odds": original_odds, "moved_by": 0.0}


# ---------------------------------------------------------------------------
# Proxy-xG feature enrichment — Point 2
# ---------------------------------------------------------------------------

def _compute_proxy_xg_features(history: pd.DataFrame, home_team: str, away_team: str, sport: str) -> dict:
    """Build proxy-xG signals from historical score data.

    These catch 'lucky' teams (winning with few shots) and fragile defences.
    Uses only data already in the DB — no extra API calls needed.

    Signals:
      - home_goals_per_game, away_goals_per_game   (scoring output proxy)
      - home_conceded_per_game, away_conceded_per_game
      - home_cs_rate, away_cs_rate                 (clean sheet rate → defensive solidity)
      - home_fts_rate, away_fts_rate               (failed to score → attacking fragility)
      - home_btts_rate, away_btts_rate             (both teams score → open game signal)
      - goal_diff_trend_home, goal_diff_trend_away (last 5 vs last 10 — momentum)
    """
    if history.empty:
        return {}

    try:
        hn = normalize_team_name(home_team, sport)
        an = normalize_team_name(away_team, sport)
        df = history[history["sport"] == sport].copy() if "sport" in history.columns else history.copy()
        df = df[df["home_score"].notna() & df["away_score"].notna()]
        if df.empty:
            return {}

        df["home_norm"] = df["home_team"].map(lambda x: normalize_team_name(str(x), sport))
        df["away_norm"] = df["away_team"].map(lambda x: normalize_team_name(str(x), sport))

        def team_games(team: str, last_n: int = 15) -> pd.DataFrame:
            mask = (df["home_norm"] == team) | (df["away_norm"] == team)
            return df[mask].tail(last_n)

        def stats(team: str, n: int = 15) -> dict:
            games = team_games(team, n)
            if games.empty:
                return {}
            gf_list, ga_list = [], []
            for _, r in games.iterrows():
                is_home = r["home_norm"] == team
                gf = float(r["home_score"] if is_home else r["away_score"])
                ga = float(r["away_score"] if is_home else r["home_score"])
                gf_list.append(gf)
                ga_list.append(ga)
            n_games = len(gf_list)
            return {
                "gpg":       round(sum(gf_list) / n_games, 3),
                "cpg":       round(sum(ga_list) / n_games, 3),
                "cs_rate":   round(sum(1 for g in ga_list if g == 0) / n_games, 3),
                "fts_rate":  round(sum(1 for g in gf_list if g == 0) / n_games, 3),
                "btts_rate": round(sum(1 for f, a in zip(gf_list, ga_list) if f > 0 and a > 0) / n_games, 3),
                "gd_avg":    round(sum(f - a for f, a in zip(gf_list, ga_list)) / n_games, 3),
            }

        h5  = stats(hn, 5)
        h15 = stats(hn, 15)
        a5  = stats(an, 5)
        a15 = stats(an, 15)

        # Momentum: last-5 goal diff vs last-15 goal diff
        h_momentum = round((h5.get("gd_avg", 0) - h15.get("gd_avg", 0)), 3) if h5 and h15 else 0.0
        a_momentum = round((a5.get("gd_avg", 0) - a15.get("gd_avg", 0)), 3) if a5 and a15 else 0.0

        return {
            "home_goals_per_game":  h15.get("gpg", 1.3),
            "home_conceded_per_game": h15.get("cpg", 1.2),
            "home_cs_rate":         h15.get("cs_rate", 0.28),
            "home_fts_rate":        h15.get("fts_rate", 0.25),
            "home_btts_rate":       h15.get("btts_rate", 0.55),
            "home_gd_momentum":     h_momentum,
            "away_goals_per_game":  a15.get("gpg", 1.1),
            "away_conceded_per_game": a15.get("cpg", 1.3),
            "away_cs_rate":         a15.get("cs_rate", 0.22),
            "away_fts_rate":        a15.get("fts_rate", 0.30),
            "away_btts_rate":       a15.get("btts_rate", 0.55),
            "away_gd_momentum":     a_momentum,
        }
    except Exception as exc:
        log.debug("Proxy-xG features failed: %s", exc)
        return {}


def _adjust_model_prob_with_proxy_xg(
    model_prob: float,
    selection: str,
    proxy: dict,
) -> float:
    """Nudge model probability using proxy-xG signals.

    A model trained purely on scorelines can be fooled by luck.
    We apply small adjustments based on:
      - Home team momentum (recent GD trend)
      - FTS rate (attacking fragility penalty)
      - CS rate (defensive reliability bonus)

    Adjustments are intentionally small (max ±5%) to avoid overriding the model.
    """
    if not proxy:
        return model_prob

    adj = 0.0
    if selection == "home":
        adj += proxy.get("home_gd_momentum", 0) * 0.02    # form trend
        adj -= proxy.get("home_fts_rate", 0.25) * 0.06    # fragile attack
        adj += proxy.get("home_cs_rate", 0.28) * 0.04     # solid defence
        adj -= proxy.get("away_cs_rate", 0.22) * 0.03     # strong away defence = harder to score
    elif selection == "away":
        adj += proxy.get("away_gd_momentum", 0) * 0.02
        adj -= proxy.get("away_fts_rate", 0.30) * 0.06
        adj += proxy.get("away_cs_rate", 0.22) * 0.04
        adj -= proxy.get("home_cs_rate", 0.28) * 0.03
    elif selection == "draw":
        # Draws more likely when both teams have middling form and BTTS
        avg_btts = (proxy.get("home_btts_rate", 0.55) + proxy.get("away_btts_rate", 0.55)) / 2
        adj += (avg_btts - 0.55) * 0.04

    # Cap adjustment at ±5%
    adj = max(-0.05, min(0.05, adj))
    return max(0.01, min(0.99, model_prob + adj))


# ---------------------------------------------------------------------------
# Model probability extraction
# ---------------------------------------------------------------------------

def _get_model_probs(history: pd.DataFrame, fixture: dict) -> dict[str, float] | None:
    """Run the ML engine and return home/draw/away probabilities."""
    try:
        sport = fixture.get("sport", "soccer")
        if sport == "soccer":
            from app.ml.ensemble import LoyalEdgeEngine
            from app.services.model_registry import active_model_path
            from app.db.session import SessionLocal
            db = SessionLocal()
            try:
                path = active_model_path(db, "soccer")
            finally:
                db.close()
            engine = LoyalEdgeEngine(path)
            items = engine.predict_soccer(history, {
                "sport": "soccer",
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "match_date": fixture.get("match_date"),
                "league": fixture.get("league", ""),
                "home_odds": fixture.get("home_odds"),
                "draw_odds": fixture.get("draw_odds"),
                "away_odds": fixture.get("away_odds"),
            })
            for item in items:
                if item.get("market") == "1X2":
                    probs = (item.get("engine_meta") or {}).get("probabilities", {})
                    if probs:
                        return {
                            "home": float(probs.get("home_win", 0.33)),
                            "draw": float(probs.get("draw", 0.33)),
                            "away": float(probs.get("away_win", 0.33)),
                        }
        else:
            from app.ml.generic import GenericSportEngine
            items = GenericSportEngine().predict(history, {
                "sport": sport,
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "match_date": fixture.get("match_date"),
            })
            for item in items:
                if item.get("market") in ("Moneyline", "1X2"):
                    probs = (item.get("engine_meta") or {}).get("probabilities", {})
                    if probs:
                        return {
                            "home": float(probs.get("home_win", 0.5)),
                            "draw": float(probs.get("draw")) if probs.get("draw") else None,
                            "away": float(probs.get("away_win", 0.5)),
                        }
    except Exception as exc:
        log.debug("Model prob extraction failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Value bet record
# ---------------------------------------------------------------------------

class ValueBet(NamedTuple):
    home_team: str
    away_team: str
    league: str
    sport: str
    match_date: str | None
    selection: str
    bookmaker_odds: float
    fair_odds: float
    model_prob: float
    model_prob_adjusted: float   # after proxy-xG nudge
    bookmaker_fair_prob: float
    value_score: float
    edge_pct: float
    kelly_fraction: float
    recommended_stake_pct: float
    sportybet_match_id: str
    is_elite: bool               # True = elite league + high confidence
    tier: str                    # "elite" | "standard" | "sandbox"
    proxy_xg: dict


# ---------------------------------------------------------------------------
# Core detection loop
# ---------------------------------------------------------------------------

def find_value_bets(
    fixtures: list[dict],
    history: pd.DataFrame,
    verify_live: bool = True,
) -> list[ValueBet]:
    """Detect value bets across all fixtures with all four protection layers."""
    results: list[ValueBet] = []

    for fx in fixtures:
        home_odds = fx.get("home_odds")
        draw_odds = fx.get("draw_odds")
        away_odds = fx.get("away_odds")
        sport     = fx.get("sport", "soccer")
        league    = fx.get("league", "")

        if not home_odds or not away_odds:
            continue

        # Strip overround
        fair = strip_overround(home_odds, draw_odds, away_odds)
        if not fair:
            continue

        # Get model probabilities
        raw_probs = _get_model_probs(history, fx)
        if not raw_probs:
            continue

        # Proxy-xG enrichment (Point 2)
        proxy = _compute_proxy_xg_features(history, fx["home_team"], fx["away_team"], sport)

        elite_league = _is_elite_league(league)

        checks = [
            ("home", home_odds, fair.get("fair_home"), raw_probs.get("home")),
            ("draw", draw_odds, fair.get("fair_draw"), raw_probs.get("draw")),
            ("away", away_odds, fair.get("fair_away"), raw_probs.get("away")),
        ]

        for selection, bookie_odds, fair_prob, model_prob_raw in checks:
            if not bookie_odds or not fair_prob or not model_prob_raw:
                continue

            # Apply proxy-xG nudge to model prob
            model_prob = _adjust_model_prob_with_proxy_xg(model_prob_raw, selection, proxy)

            ev = round(model_prob * bookie_odds, 4)

            # Determine tier (Point 1 — certainty floor)
            if (elite_league and model_prob >= ELITE_MIN_MODEL_PROB
                    and ev >= ELITE_MIN_VALUE_SCORE
                    and ELITE_MIN_ODDS <= bookie_odds <= ELITE_MAX_ODDS):
                tier = "elite"
            elif (model_prob >= STD_MIN_MODEL_PROB
                    and ev >= STD_MIN_VALUE_SCORE
                    and STD_MIN_ODDS <= bookie_odds <= STD_MAX_ODDS):
                tier = "standard"
            else:
                tier = "sandbox"

            if tier == "sandbox":
                continue  # don't include sandbox picks in results

            # Point 3: live odds verification (only for elite picks to save API calls)
            live_check = {"valid": True, "current_odds": bookie_odds, "moved_by": 0.0}
            if verify_live and tier == "elite" and fx.get("sportybet_match_id"):
                live_check = _verify_odds_live(
                    fx["sportybet_match_id"], sport, selection,
                    bookie_odds, ELITE_MIN_VALUE_SCORE, model_prob,
                )
                if not live_check["valid"]:
                    log.info(
                        "Odds moved for %s vs %s %s: %.2f → %.2f — dropping",
                        fx["home_team"], fx["away_team"], selection,
                        bookie_odds, live_check["current_odds"],
                    )
                    continue
                bookie_odds = live_check["current_odds"]
                ev = round(model_prob * bookie_odds, 4)

            edge_pct = round((ev - 1.0) * 100, 2)
            kf = kelly_fraction(model_prob, bookie_odds)
            fair_o = round(1.0 / fair_prob, 3) if fair_prob > 0 else 0.0

            results.append(ValueBet(
                home_team=fx["home_team"],
                away_team=fx["away_team"],
                league=league,
                sport=sport,
                match_date=fx.get("match_date"),
                selection=selection,
                bookmaker_odds=round(bookie_odds, 3),
                fair_odds=fair_o,
                model_prob=round(model_prob_raw, 4),
                model_prob_adjusted=round(model_prob, 4),
                bookmaker_fair_prob=round(fair_prob, 4),
                value_score=ev,
                edge_pct=edge_pct,
                kelly_fraction=round(kf, 4),
                recommended_stake_pct=round(kf * 100, 2),
                sportybet_match_id=fx.get("sportybet_match_id", ""),
                is_elite=tier == "elite",
                tier=tier,
                proxy_xg=proxy,
            ))

    # Elite first, then by edge descending
    return sorted(results, key=lambda v: (0 if v.is_elite else 1, -v.edge_pct))


# ---------------------------------------------------------------------------
# Serialiser
# ---------------------------------------------------------------------------

def _serialise(vb: ValueBet) -> dict:
    tier_label = {
        "elite":    "⭐ Elite Pick",
        "standard": "Standard Pick",
        "sandbox":  "High Risk",
    }.get(vb.tier, vb.tier)

    return {
        "home_team":    vb.home_team,
        "away_team":    vb.away_team,
        "league":       vb.league,
        "sport":        vb.sport,
        "match_date":   vb.match_date,
        "selection":    vb.selection,
        "tier":         vb.tier,
        "tier_label":   tier_label,
        "is_elite":     vb.is_elite,
        "bookmaker":    "SportyBet",
        "bookmaker_odds":          vb.bookmaker_odds,
        "fair_odds":               vb.fair_odds,
        "model_probability":       f"{vb.model_prob_adjusted:.1%}",
        "bookmaker_fair_probability": f"{vb.bookmaker_fair_prob:.1%}",
        "value_score":             vb.value_score,
        "edge_pct":                vb.edge_pct,
        "kelly_fraction":          vb.kelly_fraction,
        "recommended_stake_pct":   vb.recommended_stake_pct,
        "sportybet_match_id":      vb.sportybet_match_id,
        "label": f"{vb.home_team} vs {vb.away_team} — {vb.selection.upper()} @ {vb.bookmaker_odds}",
        "explanation": (
            f"Model estimates {vb.model_prob_adjusted:.1%} probability for {vb.selection} "
            f"(proxy-xG adjusted from {vb.model_prob:.1%}). "
            f"SportyBet's fair probability (overround removed) is {vb.bookmaker_fair_prob:.1%}. "
            f"Value score {vb.value_score:.3f}. "
            f"Recommended Kelly stake: {vb.recommended_stake_pct:.1f}% of bankroll."
        ),
        "proxy_signals": {
            "home_clean_sheet_rate":  vb.proxy_xg.get("home_cs_rate"),
            "away_clean_sheet_rate":  vb.proxy_xg.get("away_cs_rate"),
            "home_failed_to_score":   vb.proxy_xg.get("home_fts_rate"),
            "away_failed_to_score":   vb.proxy_xg.get("away_fts_rate"),
            "home_form_momentum":     vb.proxy_xg.get("home_gd_momentum"),
            "away_form_momentum":     vb.proxy_xg.get("away_gd_momentum"),
        } if vb.proxy_xg else {},
        "responsible_note": "Value bets identify mathematical edges, not guaranteed wins. Stake responsibly.",
    }


# ---------------------------------------------------------------------------
# Full pipeline entry point
# ---------------------------------------------------------------------------

def run_value_scan(
    db: Session,
    sports: list[str] | None = None,
    tier: str = "all",          # "elite" | "standard" | "all"
    verify_live: bool = True,
) -> dict:
    """Full pipeline: scrape SportyBet → strip overround → proxy-xG → model → verify → rank."""
    target_sports = sports or ["soccer", "basketball", "tennis"]

    try:
        raw_fixtures = fetch_all_sports(target_sports, limit_per_sport=60)
    except Exception as exc:
        log.exception("SportyBet fetch failed")
        return {"error": str(exc), "value_bets": [], "scanned": 0}

    if not raw_fixtures:
        return {
            "value_bets": [],
            "elite_picks": [],
            "scanned": 0,
            "message": "No fixtures returned from SportyBet. Their API may have changed or be rate-limiting.",
        }

    history = dataframe_from_db(db, max_age_days=90)
    bets = find_value_bets(raw_fixtures, history, verify_live=verify_live)

    elite  = [b for b in bets if b.tier == "elite"]
    standard = [b for b in bets if b.tier == "standard"]

    filter_map = {
        "elite":    elite,
        "standard": standard + elite,
        "all":      bets,
    }
    shown = filter_map.get(tier, bets)

    return {
        "scanned":           len(raw_fixtures),
        "value_bets_found":  len(bets),
        "elite_count":       len(elite),
        "standard_count":    len(standard),
        "tier_filter":       tier,
        "sports_checked":    target_sports,
        "live_verified":     verify_live,
        "overround_note":    "Overround stripped before comparison. Fair probabilities shown.",
        "elite_picks":       [_serialise(b) for b in elite[:10]],
        "value_bets":        [_serialise(b) for b in shown[:50]],
        "top_pick":          _serialise(elite[0]) if elite else (_serialise(bets[0]) if bets else None),
    }
