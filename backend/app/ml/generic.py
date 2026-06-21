import pandas as pd

from app.utils.team_names import normalize_team_name


def _risk(confidence_pct: float) -> str:
    return "Low" if confidence_pct >= 72 else "Medium" if confidence_pct >= 58 else "High"


class GenericSportEngine:
    """Routes each sport to its dedicated engine, falls back to form-based heuristic.

    Sport routing:
      basketball         -> BasketballEngine  (scoring/margin/spread/totals)
      tennis             -> TennisEngine      (player form/surface/H2H/sets)
      cricket            -> CricketEngine     (format/run margin/H2H)
      baseball           -> BaseballEngine    (run diff/run line/totals)
      american_football  -> AmericanFootballEngine
      hockey             -> HockeyEngine
      rugby / volleyball
      / handball / mma
      / motorsport       -> sport-aware heuristic with multi-market output
      everything else    -> form heuristic with multi-market output
    """

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        sport = fixture.get("sport") or "sport"

        # --- Dedicated engines ---
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

        # --- Form heuristic for rugby, volleyball, handball, mma, etc. ---
        return self._heuristic(history, fixture, sport)
    def _heuristic(self, history: pd.DataFrame, fixture: dict, sport: str) -> list[dict]:
        home = normalize_team_name(fixture["home_team"], sport)
        away = normalize_team_name(fixture["away_team"], sport)

        df = history[history["sport"] == sport].copy() if (not history.empty and "sport" in history.columns) else (history.copy() if not history.empty else pd.DataFrame())

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
                        return 0.50, 0.0
                    wins, margin = 0, 0.0
                    for _, row in games.iterrows():
                        is_home = row["home_norm"] == team
                        gf = float(row["home_score"] if is_home else row["away_score"])
                        ga = float(row["away_score"] if is_home else row["home_score"])
                        wins += 1 if gf > ga else 0
                        margin += gf - ga
                    return wins / len(games), margin / len(games)

                home_wr, home_margin = team_stats(home)
                away_wr, away_margin = team_stats(away)

                team_rows = played[
                    (played["home_norm"].isin([home, away])) |
                    (played["away_norm"].isin([home, away]))
                ].tail(30)
                if not team_rows.empty:
                    projected_total = round(float((team_rows["home_score"] + team_rows["away_score"]).mean()), 1)

        edge = (home_wr - away_wr) + ((home_margin - away_margin) / 20) + 0.04
        home_win_prob = max(0.28, min(0.78, 0.50 + edge / 2))

        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        winner_pick = "Home Win" if home_win_prob >= 0.5 else "Away Win"
        away_win_prob = 1 - home_win_prob

        note = (
            f"{sport.replace('_', ' ').title()} model checks recent win rate, scoring margin, and home advantage."
            if has_data else
            f"Limited {sport.replace('_', ' ')} history — conservative read based on home-side advantage signal."
        )

        meta = {
            "summary": note,
            "factors": [
                {"label": "Home win-rate", "value": f"{home_wr:.0%}", "note": f"Last 12 completed matches for {home}"},
                {"label": "Away win-rate", "value": f"{away_wr:.0%}", "note": f"Last 12 completed matches for {away}"},
                {"label": "Home scoring margin", "value": round(home_margin, 2), "note": "Average margin per game"},
                {"label": "Away scoring margin", "value": round(away_margin, 2), "note": "Average margin per game"},
                {"label": "Home advantage", "value": "+4%", "note": "Generic home-venue boost"},
            ],
            "probabilities": {
                "home_win": round(home_win_prob, 4),
                "away_win": round(away_win_prob, 4),
            },
            "market_logic": "Moneyline uses recent win-rate, scoring margin differential, and home advantage.",
        }

        items = [
            {
                "market": "Moneyline",
                "pick": winner_pick,
                "confidence": round(winner_conf, 1),
                "edge_score": round(winner_conf, 1),
                "risk_level": _risk(winner_conf),
                "reasoning": f"{note} Leans {winner_pick}: home {home_wr:.0%} win-rate, away {away_wr:.0%}, margin edge {home_margin - away_margin:.2f}.",
                "engine_meta": meta,
            }
        ]

        # Double-chance: useful for any two-outcome sport
        dc_prob = max(home_win_prob + 0.25, away_win_prob + 0.25)  # covers draw scenario
        dc_pick = "Home or Draw" if home_win_prob >= away_win_prob else "Away or Draw"
        dc_conf = round(min(dc_prob, 0.82) * 100, 1)
        items.append({
            "market": "Double Chance",
            "pick": dc_pick,
            "confidence": dc_conf,
            "edge_score": dc_conf,
            "risk_level": _risk(dc_conf),
            "reasoning": f"Double chance covers the favourite plus draw outcome. {note}",
            "engine_meta": {**meta, "market_logic": "Double chance reduces upset risk by covering two outcomes."},
        })

        # Totals market when we have projected data
        if projected_total:
            # Pick a sensible sport-aware line
            sport_lines = {
                "rugby": 40.5, "volleyball": 152.5, "handball": 52.5,
                "mma": 2.5, "motorsport": 2.5,
            }
            line = sport_lines.get(sport, projected_total * 0.95)
            over_under = "Over" if projected_total > line else "Under"
            total_conf = round(min(67.0, max(54.0, abs(projected_total - line) * 3 + 54.0)), 1)
            items.append({
                "market": "Total Points",
                "pick": f"{over_under} {line}",
                "confidence": total_conf,
                "edge_score": total_conf,
                "risk_level": _risk(total_conf),
                "reasoning": f"Recent {sport.replace('_', ' ')} sample projects {projected_total} combined score vs line {line}.",
                "engine_meta": {**meta, "market_logic": "Total uses recent combined scoring average versus a sport-adjusted line."},
            })

        return items
