import pandas as pd

from app.ml.history_guard import sanitize_prediction_history
from app.utils.team_names import normalize_team_name


def _risk(confidence_pct: float) -> str:
    return "Low" if confidence_pct >= 72 else "Medium" if confidence_pct >= 58 else "High"


def _prediction_history(history: pd.DataFrame, fixture: dict) -> pd.DataFrame:
    return sanitize_prediction_history(history, fixture)


class GenericSportEngine:
    """Routes each sport to its dedicated engine, falls back to form-based heuristic.

    Every routed engine receives the same point-in-time history snapshot so
    future/unfinished fixtures cannot contaminate recent-form or H2H features.
    """

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        sport = fixture.get("sport") or "sport"
        history = _prediction_history(history, fixture)

        if sport == "basketball":
            from app.ml.basketball import BasketballEngine
            return BasketballEngine().predict(history, fixture)

        if sport == "tennis":
            from app.ml.sport_specific import TennisEngine
            return TennisEngine().predict(history, fixture)

        if sport == "cricket":
            from app.ml.sport_specific import CricketEngine
            return CricketEngine().predict(history, fixture)

        if sport == "baseball":
            from app.ml.sport_specific import BaseballEngine
            return BaseballEngine().predict(history, fixture)

        if sport == "american_football":
            from app.ml.sport_specific import AmericanFootballEngine
            return AmericanFootballEngine().predict(history, fixture)

        if sport == "hockey":
            from app.ml.sport_specific import HockeyEngine
            return HockeyEngine().predict(history, fixture)

        return self._heuristic(history, fixture, sport)

    def _heuristic(self, history: pd.DataFrame, fixture: dict, sport: str) -> list[dict]:
        home = normalize_team_name(fixture["home_team"], sport)
        away = normalize_team_name(fixture["away_team"], sport)
        df = history.copy() if not history.empty else pd.DataFrame()

        home_wr = away_wr = 0.50
        home_margin = away_margin = 0.0
        projected_total = None
        has_data = False

        if not df.empty:
            df["home_norm"] = df["home_team"].map(lambda x: normalize_team_name(str(x), sport))
            df["away_norm"] = df["away_team"].map(lambda x: normalize_team_name(str(x), sport))
            played = df[df["home_score"].notna() & df["away_score"].notna()].copy()
            if not played.empty:
                has_data = True

                def team_stats(team: str):
                    games = played[(played["home_norm"] == team) | (played["away_norm"] == team)].tail(12)
                    if games.empty:
                        return 0.0, 0.0, 0.0
                    wins, draws, margin = 0, 0, 0.0
                    for _, row in games.iterrows():
                        is_home = row["home_norm"] == team
                        gf = float(row["home_score"] if is_home else row["away_score"])
                        ga = float(row["away_score"] if is_home else row["home_score"])
                        wins += 1 if gf > ga else 0
                        draws += 1 if gf == ga else 0
                        margin += gf - ga
                    n = len(games)
                    return wins / n, margin / n, draws / n

                home_wr, home_margin, home_dr = team_stats(home)
                away_wr, away_margin, away_dr = team_stats(away)
                team_rows = played[
                    (played["home_norm"].isin([home, away])) |
                    (played["away_norm"].isin([home, away]))
                ].tail(30)
                if not team_rows.empty:
                    projected_total = round(float((team_rows["home_score"] + team_rows["away_score"]).mean()), 1)

        # Honesty gate: without completed matches for either side there is no
        # evidence to publish — the old path fabricated league-average picks
        # with a confidence cap to look safe. Publish nothing instead.
        if not has_data:
            return []

        # Probabilities computed ONLY from completed matches: observed win and
        # draw rates, normalized. No clamps, no invented home-venue boost, no
        # league-average defaults.
        raw_home, raw_away = home_wr, away_wr
        raw_draw = (home_dr + away_dr) / 2
        total = raw_home + raw_away + raw_draw
        if total <= 0:
            return []
        home_win_prob = raw_home / total
        away_win_prob = raw_away / total
        draw_prob = raw_draw / total
        winner_pick = "Home Win" if home_win_prob >= away_win_prob else "Away Win"
        winner_conf = max(home_win_prob, away_win_prob) * 100
        note = f"{sport.replace('_', ' ').title()} model uses only completed matches: observed win rate, draw rate, and scoring margin."
        meta = {
            "summary": note,
            "factors": [
                {"label": "Home win-rate", "value": f"{home_wr:.0%}", "note": f"Last 12 completed matches for {home}"},
                {"label": "Away win-rate", "value": f"{away_wr:.0%}", "note": f"Last 12 completed matches for {away}"},
                {"label": "Draw rate (avg)", "value": f"{(home_dr + away_dr) / 2:.0%}", "note": "Observed drawn matches share, both sides"},
                {"label": "Home scoring margin", "value": round(home_margin, 2), "note": "Average margin per game"},
                {"label": "Away scoring margin", "value": round(away_margin, 2), "note": "Average margin per game"},
            ],
            "probabilities": {
                "home_win": round(home_win_prob, 4),
                "draw": round(draw_prob, 4),
                "away_win": round(away_win_prob, 4),
            },
            "market_logic": "Moneyline uses observed win/draw rates from completed matches only; no defaults, no home-venue boost.",
        }
        items = [{
            "market": "Moneyline", "pick": winner_pick, "confidence": round(winner_conf, 1),
            "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf),
            "reasoning": f"{note} Leans {winner_pick}: home {home_wr:.0%} win-rate, away {away_wr:.0%}, margin edge {home_margin - away_margin:.2f}.",
            "engine_meta": meta,
        }]
        dc_options = {
            "Home or Draw": home_win_prob + draw_prob,
            "Away or Draw": away_win_prob + draw_prob,
            "Home or Away": home_win_prob + away_win_prob,
        }
        dc_pick, dc_prob = max(dc_options.items(), key=lambda x: x[1])
        dc_conf = round(dc_prob * 100, 1)
        items.append({
            "market": "Double Chance", "pick": dc_pick, "confidence": dc_conf,
            "edge_score": dc_conf, "risk_level": _risk(dc_conf),
            "reasoning": f"Double chance computed from observed win and draw rates. {note}",
            "engine_meta": {**meta, "market_logic": "Double chance sums normalized outcome probabilities; no bonuses applied."},
        })
        if projected_total:
            sport_lines = {"rugby": 40.5, "volleyball": 152.5, "handball": 52.5, "mma": 2.5, "motorsport": 2.5}
            line = sport_lines.get(sport, projected_total * 0.95)
            over_under = "Over" if projected_total > line else "Under"
            # 50 = coin-flip baseline when the projection equals the line; it
            # only rises with actual separation between projection and line.
            total_conf = round(min(67.0, max(50.0, abs(projected_total - line) * 3 + 50.0)), 1)
            items.append({
                "market": "Total Points", "pick": f"{over_under} {line}", "confidence": total_conf,
                "edge_score": total_conf, "risk_level": _risk(total_conf),
                "reasoning": f"Recent {sport.replace('_', ' ')} sample projects {projected_total} combined score vs line {line}.",
                "engine_meta": {**meta, "market_logic": "Total uses recent combined scoring average versus a sport-adjusted line."},
            })
        return items
