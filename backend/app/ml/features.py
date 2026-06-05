import pandas as pd

from app.utils.team_names import normalize_team_name


def normalize_result(home_score: int, away_score: int) -> int:
    if home_score > away_score:
        return 2
    if home_score == away_score:
        return 1
    return 0


def build_soccer_features(fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    df = fixtures[fixtures.get("sport", "soccer") == "soccer"].sort_values("match_date").copy()
    rows, y = [], []
    team_hist: dict[str, list[dict]] = {}
    for _, r in df.iterrows():
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        home = normalize_team_name(r["home_team"], "soccer")
        away = normalize_team_name(r["away_team"], "soccer")
        hh, ah = team_hist.get(home, [])[-10:], team_hist.get(away, [])[-10:]

        def avg(hist, key, default):
            return sum(x[key] for x in hist) / len(hist) if hist else default

        rows.append({
            "home_form_points": avg(hh, "points", 1.2),
            "away_form_points": avg(ah, "points", 1.2),
            "home_goals_for": avg(hh, "gf", 1.3),
            "home_goals_against": avg(hh, "ga", 1.2),
            "away_goals_for": avg(ah, "gf", 1.1),
            "away_goals_against": avg(ah, "ga", 1.3),
            "home_implied": 1 / r["home_odds"] if r.get("home_odds") else 0.0,
            "draw_implied": 1 / r["draw_odds"] if r.get("draw_odds") else 0.0,
            "away_implied": 1 / r["away_odds"] if r.get("away_odds") else 0.0,
        })
        hs, aas = int(r["home_score"]), int(r["away_score"])
        y.append(normalize_result(hs, aas))
        team_hist.setdefault(home, []).append({"gf": hs, "ga": aas, "points": 3 if hs > aas else 1 if hs == aas else 0})
        team_hist.setdefault(away, []).append({"gf": aas, "ga": hs, "points": 3 if aas > hs else 1 if hs == aas else 0})
    return pd.DataFrame(rows).fillna(0), pd.Series(y)


def features_for_fixture(history: pd.DataFrame, home_team: str, away_team: str) -> dict:
    home_team = normalize_team_name(home_team, "soccer")
    away_team = normalize_team_name(away_team, "soccer")
    hist = history[history.get("sport", "soccer") == "soccer"].sort_values("match_date") if not history.empty else history

    def recent(team):
        if hist.empty:
            return []
        h = hist[((hist.home_team == team) | (hist.away_team == team)) & hist.home_score.notna()].tail(10)
        rows = []
        for _, r in h.iterrows():
            rows.append((r.home_score, r.away_score) if r.home_team == team else (r.away_score, r.home_score))
        return rows

    def avg(rows, idx, default):
        return sum(x[idx] for x in rows) / len(rows) if rows else default

    hh, ah = recent(home_team), recent(away_team)
    return {
        "home_form_points": 1.5,
        "away_form_points": 1.2,
        "home_goals_for": avg(hh, 0, 1.3),
        "home_goals_against": avg(hh, 1, 1.2),
        "away_goals_for": avg(ah, 0, 1.1),
        "away_goals_against": avg(ah, 1, 1.3),
        "home_implied": 0.0,
        "draw_implied": 0.0,
        "away_implied": 0.0,
    }
