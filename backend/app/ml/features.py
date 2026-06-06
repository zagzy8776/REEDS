import pandas as pd

from app.utils.team_names import normalize_team_name


def _avg_dict(hist: list[dict], key: str, default: float, window: int | None = None) -> float:
    rows = hist[-window:] if window else hist
    return sum(x[key] for x in rows) / len(rows) if rows else default


def _rate_dict(hist: list[dict], key: str, value, default: float, window: int | None = None) -> float:
    rows = hist[-window:] if window else hist
    return sum(1 for x in rows if x[key] == value) / len(rows) if rows else default


def _condition_rate(hist: list[dict], predicate, default: float, window: int | None = None) -> float:
    rows = hist[-window:] if window else hist
    return sum(1 for x in rows if predicate(x)) / len(rows) if rows else default


def _elo_expected(a: float, b: float) -> float:
    return 1 / (1 + 10 ** ((b - a) / 400))


def _update_elo(a: float, b: float, score_a: float, k: float = 24) -> tuple[float, float]:
    expected_a = _elo_expected(a, b)
    expected_b = 1 - expected_a
    return a + k * (score_a - expected_a), b + k * ((1 - score_a) - expected_b)


SOCCER_LEAGUE_DIFFICULTY = {
    "EPL": 1.00,
    "LA_LIGA": 0.98,
    "La Liga": 0.98,
    "SERIE_A": 0.96,
    "Serie A": 0.96,
    "BUNDESLIGA": 0.96,
    "LIGUE_1": 0.92,
    "CHAMPIONSHIP": 0.85,
    "EREDIVISIE": 0.84,
    "PORTUGAL": 0.82,
    "BELGIUM": 0.78,
    "SCOTLAND": 0.76,
    "TURKEY": 0.80,
}


def _soccer_league_difficulty(league) -> float:
    return SOCCER_LEAGUE_DIFFICULTY.get(str(league), 0.75)


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
    team_home_hist: dict[str, list[dict]] = {}
    team_away_hist: dict[str, list[dict]] = {}
    team_elo: dict[str, float] = {}
    for _, r in df.iterrows():
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        home = normalize_team_name(r["home_team"], "soccer")
        away = normalize_team_name(r["away_team"], "soccer")
        hh, ah = team_hist.get(home, [])[-10:], team_hist.get(away, [])[-10:]
        h_home, a_away = team_home_hist.get(home, [])[-10:], team_away_hist.get(away, [])[-10:]
        home_elo, away_elo = team_elo.get(home, 1500.0), team_elo.get(away, 1500.0)
        league_difficulty = _soccer_league_difficulty(r.get("league"))
        normalized_home_elo = home_elo * league_difficulty
        normalized_away_elo = away_elo * league_difficulty
        hs, aas = int(r["home_score"]), int(r["away_score"])
        home_result = 1.0 if hs > aas else 0.5 if hs == aas else 0.0

        rows.append({
            "home_form_points": _avg_dict(hh, "points", 1.2),
            "away_form_points": _avg_dict(ah, "points", 1.2),
            "home_form_points_3": _avg_dict(hh, "points", 1.2, 3),
            "away_form_points_3": _avg_dict(ah, "points", 1.2, 3),
            "home_form_points_5": _avg_dict(hh, "points", 1.2, 5),
            "away_form_points_5": _avg_dict(ah, "points", 1.2, 5),
            "home_win_rate_5": _rate_dict(hh, "result", "W", 0.38, 5),
            "away_win_rate_5": _rate_dict(ah, "result", "W", 0.32, 5),
            "home_draw_rate_5": _rate_dict(hh, "result", "D", 0.28, 5),
            "away_draw_rate_5": _rate_dict(ah, "result", "D", 0.28, 5),
            "home_loss_rate_5": _rate_dict(hh, "result", "L", 0.34, 5),
            "away_loss_rate_5": _rate_dict(ah, "result", "L", 0.40, 5),
            "home_goals_for": _avg_dict(hh, "gf", 1.3),
            "home_goals_against": _avg_dict(hh, "ga", 1.2),
            "away_goals_for": _avg_dict(ah, "gf", 1.1),
            "away_goals_against": _avg_dict(ah, "ga", 1.3),
            "home_home_goals_for": _avg_dict(h_home, "gf", 1.4),
            "home_home_goals_against": _avg_dict(h_home, "ga", 1.1),
            "away_away_goals_for": _avg_dict(a_away, "gf", 1.0),
            "away_away_goals_against": _avg_dict(a_away, "ga", 1.4),
            "home_goal_diff": _avg_dict(hh, "gd", 0.1),
            "away_goal_diff": _avg_dict(ah, "gd", -0.1),
            "home_clean_sheet_rate_5": _condition_rate(hh, lambda x: x["ga"] == 0, 0.28, 5),
            "away_clean_sheet_rate_5": _condition_rate(ah, lambda x: x["ga"] == 0, 0.22, 5),
            "home_failed_score_rate_5": _condition_rate(hh, lambda x: x["gf"] == 0, 0.25, 5),
            "away_failed_score_rate_5": _condition_rate(ah, lambda x: x["gf"] == 0, 0.30, 5),
            "home_unbeaten_rate_10": _condition_rate(hh, lambda x: x["result"] != "L", 0.62, 10),
            "away_unbeaten_rate_10": _condition_rate(ah, lambda x: x["result"] != "L", 0.55, 10),
            "home_elo": normalized_home_elo,
            "away_elo": normalized_away_elo,
            "elo_diff": normalized_home_elo - normalized_away_elo,
            "league_strength": league_difficulty,
            "home_implied": 1 / r["home_odds"] if r.get("home_odds") else 0.0,
            "draw_implied": 1 / r["draw_odds"] if r.get("draw_odds") else 0.0,
            "away_implied": 1 / r["away_odds"] if r.get("away_odds") else 0.0,
        })
        y.append(normalize_result(hs, aas))
        home_entry = {"gf": hs, "ga": aas, "gd": hs - aas, "points": 3 if hs > aas else 1 if hs == aas else 0, "result": "W" if hs > aas else "D" if hs == aas else "L"}
        away_entry = {"gf": aas, "ga": hs, "gd": aas - hs, "points": 3 if aas > hs else 1 if hs == aas else 0, "result": "W" if aas > hs else "D" if hs == aas else "L"}
        team_hist.setdefault(home, []).append(home_entry)
        team_hist.setdefault(away, []).append(away_entry)
        team_home_hist.setdefault(home, []).append(home_entry)
        team_away_hist.setdefault(away, []).append(away_entry)
        team_elo[home], team_elo[away] = _update_elo(home_elo, away_elo, home_result)
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
            if r.home_team == team:
                rows.append((r.home_score, r.away_score, 3 if r.home_score > r.away_score else 1 if r.home_score == r.away_score else 0))
            else:
                rows.append((r.away_score, r.home_score, 3 if r.away_score > r.home_score else 1 if r.home_score == r.away_score else 0))
        return rows

    def avg(rows, idx, default):
        return sum(x[idx] for x in rows) / len(rows) if rows else default

    hh, ah = recent(home_team), recent(away_team)
    return {
        "home_form_points": avg(hh, 2, 1.5),
        "away_form_points": avg(ah, 2, 1.2),
        "home_form_points_5": avg(hh[-5:], 2, 1.5),
        "away_form_points_5": avg(ah[-5:], 2, 1.2),
        "home_goals_for": avg(hh, 0, 1.3),
        "home_goals_against": avg(hh, 1, 1.2),
        "away_goals_for": avg(ah, 0, 1.1),
        "away_goals_against": avg(ah, 1, 1.3),
        "home_goal_diff": avg([(x[0] - x[1],) for x in hh], 0, 0.1),
        "away_goal_diff": avg([(x[0] - x[1],) for x in ah], 0, -0.1),
        "home_elo": 1500.0,
        "away_elo": 1500.0,
        "elo_diff": 0.0,
        "league_strength": 0,
        "home_implied": 0.0,
        "draw_implied": 0.0,
        "away_implied": 0.0,
    }


def build_basketball_features(fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    df = fixtures[fixtures.get("sport", "basketball") == "basketball"].sort_values("match_date").copy()
    rows, y = [], []
    team_hist: dict[str, list[dict]] = {}
    team_elo: dict[str, float] = {}
    last_played: dict[str, pd.Timestamp] = {}
    for _, r in df.iterrows():
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        home = normalize_team_name(r["home_team"], "basketball")
        away = normalize_team_name(r["away_team"], "basketball")
        hh, ah = team_hist.get(home, [])[-10:], team_hist.get(away, [])[-10:]
        home_elo, away_elo = team_elo.get(home, 1500.0), team_elo.get(away, 1500.0)
        game_date = pd.to_datetime(r.get("match_date"), errors="coerce")
        home_rest = (game_date - last_played[home]).days if home in last_played and not pd.isna(game_date) else 3
        away_rest = (game_date - last_played[away]).days if away in last_played and not pd.isna(game_date) else 3

        rows.append({
            "home_recent_points_for": _avg_dict(hh, "pf", 112),
            "home_recent_points_against": _avg_dict(hh, "pa", 110),
            "away_recent_points_for": _avg_dict(ah, "pf", 108),
            "away_recent_points_against": _avg_dict(ah, "pa", 112),
            "home_recent_margin": _avg_dict(hh, "margin", 2),
            "away_recent_margin": _avg_dict(ah, "margin", -2),
            "home_recent_margin_5": _avg_dict(hh, "margin", 2, 5),
            "away_recent_margin_5": _avg_dict(ah, "margin", -2, 5),
            "home_win_rate": _avg_dict(hh, "win", 0.55),
            "away_win_rate": _avg_dict(ah, "win", 0.45),
            "home_elo": home_elo,
            "away_elo": away_elo,
            "elo_diff": home_elo - away_elo,
            "home_rest_days": min(max(home_rest, 0), 7),
            "away_rest_days": min(max(away_rest, 0), 7),
            "home_back_to_back": 1 if home_rest <= 1 else 0,
            "away_back_to_back": 1 if away_rest <= 1 else 0,
        })
        hs, aas = int(r["home_score"]), int(r["away_score"])
        y.append(1 if hs > aas else 0)
        team_hist.setdefault(home, []).append({"pf": hs, "pa": aas, "margin": hs - aas, "win": 1 if hs > aas else 0})
        team_hist.setdefault(away, []).append({"pf": aas, "pa": hs, "margin": aas - hs, "win": 1 if aas > hs else 0})
        team_elo[home], team_elo[away] = _update_elo(home_elo, away_elo, 1.0 if hs > aas else 0.0, k=20)
        if not pd.isna(game_date):
            last_played[home] = game_date
            last_played[away] = game_date
    return pd.DataFrame(rows).fillna(0), pd.Series(y)


def basketball_features_for_fixture(history: pd.DataFrame, home_team: str, away_team: str) -> dict:
    home_team = normalize_team_name(home_team, "basketball")
    away_team = normalize_team_name(away_team, "basketball")
    hist = history[history.get("sport", "basketball") == "basketball"].sort_values("match_date") if not history.empty else history

    def recent(team):
        if hist.empty:
            return []
        h = hist[((hist.home_team == team) | (hist.away_team == team)) & hist.home_score.notna()].tail(10)
        rows = []
        for _, r in h.iterrows():
            pf, pa = (r.home_score, r.away_score) if r.home_team == team else (r.away_score, r.home_score)
            rows.append((pf, pa, pf - pa, 1 if pf > pa else 0))
        return rows

    def avg(rows, idx, default):
        return sum(x[idx] for x in rows) / len(rows) if rows else default

    hh, ah = recent(home_team), recent(away_team)
    return {
        "home_recent_points_for": avg(hh, 0, 112),
        "home_recent_points_against": avg(hh, 1, 110),
        "away_recent_points_for": avg(ah, 0, 108),
        "away_recent_points_against": avg(ah, 1, 112),
        "home_recent_margin": avg(hh, 2, 2),
        "away_recent_margin": avg(ah, 2, -2),
        "home_recent_margin_5": avg(hh[-5:], 2, 2),
        "away_recent_margin_5": avg(ah[-5:], 2, -2),
        "home_win_rate": avg(hh, 3, 0.55),
        "away_win_rate": avg(ah, 3, 0.45),
        "home_elo": 1500.0,
        "away_elo": 1500.0,
        "elo_diff": 0.0,
        "home_rest_days": 3,
        "away_rest_days": 3,
        "home_back_to_back": 0,
        "away_back_to_back": 0,
    }
