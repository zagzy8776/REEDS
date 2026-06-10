import pandas as pd

from app.utils.team_names import normalize_team_name


def _risk(confidence_pct: float) -> str:
    return "Low" if confidence_pct >= 72 else "Medium" if confidence_pct >= 58 else "High"


class GenericSportEngine:
    """Safe fallback engine for sports without a dedicated model yet.

    It uses historical win rate, home advantage, and recent scoring margin where
    scores exist. This keeps new sports populated without pretending we have a
    deep model for every sport on day one.
    """

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        sport = fixture.get("sport") or "sport"
        home = normalize_team_name(fixture["home_team"], sport)
        away = normalize_team_name(fixture["away_team"], sport)
        df = history[history.get("sport", sport) == sport].copy() if not history.empty else pd.DataFrame()
        if df.empty:
            home_win_prob = 0.54
            projected_total = None
            note = "Limited sport history is available, so this is a conservative market read."
            home_wr = away_wr = 0.50
            home_margin = away_margin = 0.0
        else:
            df["home_norm"] = df["home_team"].map(lambda x: normalize_team_name(str(x), sport))
            df["away_norm"] = df["away_team"].map(lambda x: normalize_team_name(str(x), sport))
            played = df[df["home_score"].notna() & df["away_score"].notna()].copy()
            team_rows = played[(played["home_norm"].isin([home, away])) | (played["away_norm"].isin([home, away]))].tail(30)

            def team_score(team: str) -> tuple[float, float]:
                games = played[(played["home_norm"] == team) | (played["away_norm"] == team)].tail(12)
                if games.empty:
                    return 0.50, 0.0
                wins = 0
                margin = 0.0
                for _, row in games.iterrows():
                    is_home = row["home_norm"] == team
                    gf = float(row["home_score"] if is_home else row["away_score"])
                    ga = float(row["away_score"] if is_home else row["home_score"])
                    wins += 1 if gf > ga else 0
                    margin += gf - ga
                return wins / len(games), margin / len(games)

            home_wr, home_margin = team_score(home)
            away_wr, away_margin = team_score(away)
            edge = (home_wr - away_wr) + ((home_margin - away_margin) / 20) + 0.04
            home_win_prob = max(0.35, min(0.72, 0.50 + edge / 2))
            projected_total = None
            if not team_rows.empty:
                projected_total = round(float((team_rows["home_score"] + team_rows["away_score"]).mean()), 1)
            note = f"{sport.replace('_', ' ').title()} fallback model checks recent win rate, scoring margin, and home advantage."

        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        winner_pick = "Home Win" if home_win_prob >= 0.5 else "Away Win"
        meta = {
            "summary": note,
            "factors": [
                {"label": "Home win-rate signal", "value": f"{home_wr:.0%}", "note": f"Recent available sample for {home}"},
                {"label": "Away win-rate signal", "value": f"{away_wr:.0%}", "note": f"Recent available sample for {away}"},
                {"label": "Home scoring margin", "value": round(home_margin, 2), "note": "Recent points/goals/runs margin where scores exist"},
                {"label": "Away scoring margin", "value": round(away_margin, 2), "note": "Recent points/goals/runs margin where scores exist"},
                {"label": "Home advantage", "value": "+4%", "note": "Small generic boost for home venue when sport-specific model is unavailable"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "market_logic": "Moneyline fallback chooses the side with the stronger recent win-rate/margin signal plus home advantage.",
        }
        items = [
            {
                "market": "Moneyline",
                "pick": winner_pick,
                "confidence": round(winner_conf, 1),
                "edge_score": round(winner_conf, 1),
                "risk_level": _risk(winner_conf),
                "reasoning": f"{note} It leans {winner_pick} from home win signal {home_wr:.0%}, away win signal {away_wr:.0%}, margin edge {home_margin - away_margin:.2f}, and a small home advantage.",
                "engine_meta": meta,
            }
        ]
        if projected_total:
            items.append({
                "market": "Total Points",
                "pick": "Projected High Total" if projected_total >= 2.5 else "Projected Low Total",
                "confidence": 56.0,
                "edge_score": 56.0,
                "risk_level": "Medium",
                "reasoning": f"Recent games in this sport/team sample average around {projected_total} total points/goals/runs, so the model expects a {'higher' if projected_total >= 2.5 else 'lower'} scoring profile.",
                "engine_meta": {**meta, "projection": {"projected_total": projected_total}, "market_logic": "Total read uses the recent combined scoring average from available completed games."},
            })
        return items