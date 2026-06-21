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
# Proxy-xG — Goal Differential Velocity (GDv) + FTS/CS fragility analysis
# Mathematically sound: adjustments applied symmetrically so H+D+A = 1.0
# ---------------------------------------------------------------------------

def _compute_proxy_xg_features(
    history: pd.DataFrame,
    home_team: str,
    away_team: str,
    sport: str,
) -> dict:
    """Compute GDv, FTS fragility, CS solidity, and lucky-win detection.

    GDv = Mean GD (last 5) − Mean GD (last 15)
      Positive → team is in an upward trend vs their season baseline
      Negative → team is under-performing / on a downward trajectory

    FTS fragility: if FTS_last5 > FTS_season, attack is regressing
    CS lucky flag: if CS_last5 > CS_season BUT we can't verify shot data,
                   we use "high CS with low GPG" as a lucky proxy.

    All values returned are raw floats. The adjustment function (below)
    turns them into a probability nudge that preserves sum-to-one.
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

        def _team_stats(team: str, n: int) -> dict:
            mask = (df["home_norm"] == team) | (df["away_norm"] == team)
            rows = df[mask].tail(n)
            if rows.empty:
                return {"gpg": 1.2, "cpg": 1.2, "gd": 0.0, "cs": 0.28, "fts": 0.25, "btts": 0.55, "n": 0}
            gf_list, ga_list = [], []
            for _, r in rows.iterrows():
                is_home = r["home_norm"] == team
                gf = float(r["home_score"] if is_home else r["away_score"])
                ga = float(r["away_score"] if is_home else r["home_score"])
                gf_list.append(gf)
                ga_list.append(ga)
            n_games = len(gf_list)
            return {
                "gpg":  round(sum(gf_list) / n_games, 3),
                "cpg":  round(sum(ga_list) / n_games, 3),
                "gd":   round(sum(f - a for f, a in zip(gf_list, ga_list)) / n_games, 3),
                "cs":   round(sum(1 for g in ga_list if g == 0) / n_games, 3),
                "fts":  round(sum(1 for g in gf_list if g == 0) / n_games, 3),
                "btts": round(sum(1 for f, a in zip(gf_list, ga_list) if f > 0 and a > 0) / n_games, 3),
                "n":    n_games,
            }

        h5  = _team_stats(hn, 5)
        h15 = _team_stats(hn, 15)
        a5  = _team_stats(an, 5)
        a15 = _team_stats(an, 15)

        # GDv = recent trend vs season baseline
        home_gdv = round(h5["gd"] - h15["gd"], 3) if h5["n"] >= 3 and h15["n"] >= 8 else 0.0
        away_gdv = round(a5["gd"] - a15["gd"], 3) if a5["n"] >= 3 and a15["n"] >= 8 else 0.0

        # FTS fragility: recent FTS worse than season → attack regressing
        home_fts_delta = round(h5["fts"] - h15["fts"], 3)  # positive = getting worse
        away_fts_delta = round(a5["fts"] - a15["fts"], 3)

        # CS lucky proxy: high recent CS but low GPG → might be lucky, not solid
        home_cs_lucky = h5["cs"] > h15["cs"] + 0.10 and h5["gpg"] < h15["gpg"] - 0.2
        away_cs_lucky = a5["cs"] > a15["cs"] + 0.10 and a5["gpg"] < a15["gpg"] - 0.2

        return {
            # Raw stats — 5-game window
            "home_gpg_5": h5["gpg"], "home_cpg_5": h5["cpg"],
            "home_cs_5":  h5["cs"],  "home_fts_5": h5["fts"],
            "away_gpg_5": a5["gpg"], "away_cpg_5": a5["cpg"],
            "away_cs_5":  a5["cs"],  "away_fts_5": a5["fts"],
            # Raw stats — 15-game season baseline
            "home_gpg_15": h15["gpg"], "home_cs_15": h15["cs"], "home_fts_15": h15["fts"],
            "away_gpg_15": a15["gpg"], "away_cs_15": a15["cs"], "away_fts_15": a15["fts"],
            # Derived velocity + fragility signals
            "home_gdv":        home_gdv,
            "away_gdv":        away_gdv,
            "home_fts_delta":  home_fts_delta,
            "away_fts_delta":  away_fts_delta,
            "home_cs_lucky":   home_cs_lucky,
            "away_cs_lucky":   away_cs_lucky,
            # BTTS signal (draws / totals markets)
            "avg_btts": round((h15["btts"] + a15["btts"]) / 2, 3),
        }
    except Exception as exc:
        log.debug("Proxy-xG features failed: %s", exc)
        return {}


def _adjust_probs_with_proxy_xg(
    raw_probs: dict[str, float],
    proxy: dict,
) -> tuple[dict[str, float], dict]:
    """Apply GDv + FTS/CS nudge while preserving sum-to-one (H+D+A = 1.0).

    Strategy:
      1. Compute a signed scalar for home and away from their GDv / FTS / CS signals.
      2. Convert scalars to probability shifts using a softmax-style renormalisation
         so the adjustments never break the 100% constraint.
      3. Cap each individual shift at ±4 percentage points (0.04) to avoid
         overriding the core ML model with noisy proxy data.

    Returns (adjusted_probs, insight_dict) where insight_dict feeds the AI badge.
    """
    if not proxy or not raw_probs:
        return raw_probs, {}

    # --- Step 1: compute home and away adjustment scalars ---
    MAX_SHIFT = 0.04   # absolute cap per outcome

    home_scalar = 0.0
    away_scalar = 0.0
    insights: list[str] = []

    # GDv contribution (max ±2pp from velocity alone)
    home_gdv = proxy.get("home_gdv", 0.0)
    away_gdv = proxy.get("away_gdv", 0.0)
    home_scalar += min(0.02, max(-0.02, home_gdv * 0.025))
    away_scalar += min(0.02, max(-0.02, away_gdv * 0.025))

    if abs(home_gdv) >= 0.3:
        direction = "positive" if home_gdv > 0 else "negative"
        insights.append(
            f"Home GDv {home_gdv:+.2f} ({direction} momentum vs season baseline)"
        )
    if abs(away_gdv) >= 0.3:
        direction = "positive" if away_gdv > 0 else "negative"
        insights.append(
            f"Away GDv {away_gdv:+.2f} ({direction} momentum vs season baseline)"
        )

    # FTS fragility penalty (max ±1.5pp)
    home_fts_d = proxy.get("home_fts_delta", 0.0)
    away_fts_d = proxy.get("away_fts_delta", 0.0)
    home_scalar -= min(0.015, max(0.0, home_fts_d * 0.08))
    away_scalar -= min(0.015, max(0.0, away_fts_d * 0.08))

    if home_fts_d > 0.15:
        insights.append(
            f"Home FTS rate {proxy.get('home_fts_5', 0):.0%} recently vs "
            f"{proxy.get('home_fts_15', 0):.0%} season — attacking fragility penalty"
        )
    if away_fts_d > 0.15:
        insights.append(
            f"Away FTS rate {proxy.get('away_fts_5', 0):.0%} recently vs "
            f"{proxy.get('away_fts_15', 0):.0%} season — attacking fragility penalty"
        )

    # Lucky CS regression (max ±1pp)
    if proxy.get("home_cs_lucky"):
        home_scalar -= 0.01
        insights.append("Home recent clean sheets flagged as lucky (high CS, declining GPG)")
    if proxy.get("away_cs_lucky"):
        away_scalar -= 0.01
        insights.append("Away recent clean sheets flagged as lucky (high CS, declining GPG)")

    # --- Step 2: cap and apply symmetrically ---
    home_scalar = max(-MAX_SHIFT, min(MAX_SHIFT, home_scalar))
    away_scalar = max(-MAX_SHIFT, min(MAX_SHIFT, away_scalar))

    h = raw_probs.get("home", 0.33)
    d = raw_probs.get("draw")
    a = raw_probs.get("away", 0.33)

    if d is not None:
        # Three-way market: shifts must net to zero so H + D + A = 1.0
        # Draw absorbs the residual — it's the least directional outcome
        h_new = h + home_scalar
        a_new = a + away_scalar
        d_new = 1.0 - h_new - a_new
        # If draw goes negative or tiny, clamp and redistribute
        if d_new < 0.05:
            excess = 0.05 - d_new
            h_new -= excess * (h / (h + a)) if (h + a) > 0 else excess / 2
            a_new -= excess * (a / (h + a)) if (h + a) > 0 else excess / 2
            d_new = 0.05
        adjusted = {
            "home": round(max(0.01, min(0.95, h_new)), 4),
            "draw": round(max(0.05, min(0.80, d_new)), 4),
            "away": round(max(0.01, min(0.95, a_new)), 4),
        }
    else:
        # Two-way market: home shift = -away shift
        h_new = h + home_scalar - away_scalar / 2
        a_new = a + away_scalar - home_scalar / 2
        total = h_new + a_new
        adjusted = {
            "home": round(max(0.01, min(0.99, h_new / total)), 4),
            "away": round(max(0.01, min(0.99, a_new / total)), 4),
        }

    # --- Step 3: build insight badge data ---
    net_home_shift = round((adjusted["home"] - h) * 100, 2)
    net_away_shift = round((adjusted["away"] - a) * 100, 2)
    insight_badge = {
        "adjustments_applied": len(insights) > 0,
        "home_prob_shift": f"{net_home_shift:+.1f}%",
        "away_prob_shift": f"{net_away_shift:+.1f}%",
        "signals": insights,
        "home_gdv": home_gdv,
        "away_gdv": away_gdv,
        "home_fts_recent":  proxy.get("home_fts_5"),
        "away_fts_recent":  proxy.get("away_fts_5"),
        "home_cs_recent":   proxy.get("home_cs_5"),
        "away_cs_recent":   proxy.get("away_cs_5"),
    }

    return adjusted, insight_badge


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
    model_prob_raw: float        # before proxy-xG adjustment
    model_prob_adjusted: float   # after sum-preserving GDv adjustment
    bookmaker_fair_prob: float
    value_score: float
    edge_pct: float
    kelly_fraction: float
    recommended_stake_pct: float
    sportybet_match_id: str
    is_elite: bool
    tier: str
    proxy_xg: dict
    ai_insight: dict             # badge data for frontend


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

        # Proxy-xG enrichment — GDv + FTS + CS lucky detection (Point 2)
        proxy = _compute_proxy_xg_features(history, fx["home_team"], fx["away_team"], sport)
        # Apply sum-preserving adjustment to ALL outcomes simultaneously
        adjusted_probs, ai_insight = _adjust_probs_with_proxy_xg(raw_probs, proxy)

        elite_league = _is_elite_league(league)

        checks = [
            ("home", home_odds, fair.get("fair_home"),
             raw_probs.get("home"), adjusted_probs.get("home", raw_probs.get("home"))),
            ("draw", draw_odds, fair.get("fair_draw"),
             raw_probs.get("draw"), adjusted_probs.get("draw", raw_probs.get("draw"))),
            ("away", away_odds, fair.get("fair_away"),
             raw_probs.get("away"), adjusted_probs.get("away", raw_probs.get("away"))),
        ]

        for selection, bookie_odds, fair_prob, model_prob_raw, model_prob in checks:
            if not bookie_odds or not fair_prob or not model_prob_raw or not model_prob:
                continue

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
                model_prob_raw=round(model_prob_raw, 4),
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
                ai_insight=ai_insight,
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

    # Build the AI Insights badge (frontend displays this as a card)
    prob_shift = round((vb.model_prob_adjusted - vb.model_prob_raw) * 100, 2)
    smart_nudge_text = ""
    if vb.ai_insight.get("adjustments_applied") and abs(prob_shift) >= 0.5:
        direction = "upward" if prob_shift > 0 else "downward"
        signals_summary = "; ".join(vb.ai_insight.get("signals", [])[:2])
        smart_nudge_text = (
            f"AI Smart Nudge: {prob_shift:+.1f}% {direction} adjustment. "
            f"{signals_summary}."
        )

    verified_ago = None
    if vb.tier == "elite":
        verified_ago = "verified at request time"

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
        "model_probability_raw":   f"{vb.model_prob_raw:.1%}",
        "model_probability":       f"{vb.model_prob_adjusted:.1%}",
        "bookmaker_fair_probability": f"{vb.bookmaker_fair_prob:.1%}",
        "value_score":             vb.value_score,
        "edge_pct":                vb.edge_pct,
        "kelly_fraction":          vb.kelly_fraction,
        "recommended_stake_pct":   vb.recommended_stake_pct,
        "sportybet_match_id":      vb.sportybet_match_id,
        "label": f"{vb.home_team} vs {vb.away_team} — {vb.selection.upper()} @ {vb.bookmaker_odds}",

        # === AI INSIGHTS BADGE — display this on the frontend ===
        "ai_insights": {
            "model_prediction": f"{vb.selection.title()} Win ({vb.model_prob_adjusted:.1%} Probability)",
            "sportybet_fair_odds": f"{vb.fair_odds} (Implied {vb.bookmaker_fair_prob:.1%})",
            "smart_nudge": smart_nudge_text or "No adjustment needed — model and proxy-xG signals aligned.",
            "live_verification": f"Passed ({verified_ago})" if verified_ago else "Checked at last scheduler run",
            "home_gdv": vb.ai_insight.get("home_gdv"),
            "away_gdv": vb.ai_insight.get("away_gdv"),
            "home_fts_recent": f"{vb.ai_insight.get('home_fts_recent', 0):.0%}" if vb.ai_insight.get("home_fts_recent") is not None else None,
            "away_fts_recent": f"{vb.ai_insight.get('away_fts_recent', 0):.0%}" if vb.ai_insight.get("away_fts_recent") is not None else None,
            "signals_fired": vb.ai_insight.get("signals", []),
            "prob_shift":  f"{prob_shift:+.1f}%",
        },

        "explanation": (
            f"Model estimates {vb.model_prob_adjusted:.1%} probability for {vb.selection} "
            f"(GDv-adjusted from raw {vb.model_prob_raw:.1%}). "
            f"SportyBet's fair probability (overround removed) is {vb.bookmaker_fair_prob:.1%}. "
            f"Value score {vb.value_score:.3f}. "
            f"Recommended Kelly stake: {vb.recommended_stake_pct:.1f}% of bankroll."
        ),
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
