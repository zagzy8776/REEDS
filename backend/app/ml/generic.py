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
        items = [
            {
                "market": "Moneyline",
                "pick": winner_pick,
                "confidence": round(winner_conf, 1),
                "edge_score": round(winner_conf, 1),
                "risk_level": _risk(winner_conf),
                "reasoning": note,
                "engine_meta": {"summary": note, "probabilities": {"home_win": home_win_prob, "away_win": 1 - home_win_prob}},
            }
        ]
        if projected_total:
            items.append({
                "market": "Total Points",
                "pick": "Projected High Total" if projected_total >= 2.5 else "Projected Low Total",
                "confidence": 56.0,
                "edge_score": 56.0,
                "risk_level": "Medium",
                "reasoning": f"Recent games in this sport/team sample average around {projected_total} total points/goals/runs.",
                "engine_meta": {"summary": note, "projection": {"projected_total": projected_total}},
            })
        return items