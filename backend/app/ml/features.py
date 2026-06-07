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


def _odds_margin(home_odds, draw_odds, away_odds) -> float:
    """Bookmaker overround/juice for 1X2 prices.

    Fair decimal odds sum to roughly 1.0 implied probability. Anything above that
    is margin. Missing/incomplete odds return 0 so historical rows without odds
    remain usable.
    """

    try:
        if not home_odds or not draw_odds or not away_odds:
            return 0.0
        return max((1 / float(home_odds)) + (1 / float(draw_odds)) + (1 / float(away_odds)) - 1, 0.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


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
            "odds_margin": _odds_margin(r.get("home_odds"), r.get("draw_odds"), r.get("away_odds")),
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


def features_for_fixture(
    history: pd.DataFrame,
    home_team: str,
    away_team: str,
    fixture_date=None,
    league: str | None = None,
    home_odds: float | None = None,
    draw_odds: float | None = None,
    away_odds: float | None = None,
) -> dict:
    home_team = normalize_team_name(home_team, "soccer")
    away_team = normalize_team_name(away_team, "soccer")
    hist = history[history.get("sport", "soccer") == "soccer"].sort_values("match_date") if not history.empty else history
    if not hist.empty and fixture_date is not None:
        cutoff = pd.to_datetime(fixture_date, errors="coerce")
        if not pd.isna(cutoff):
            hist = hist[pd.to_datetime(hist["match_date"], errors="coerce") < cutoff]

    def recent(team, venue: str | None = None):
        if hist.empty:
            return []
        normalized_home = hist.home_team.map(lambda x: normalize_team_name(str(x), "soccer"))
        normalized_away = hist.away_team.map(lambda x: normalize_team_name(str(x), "soccer"))
        if venue == "home":
            mask = normalized_home == team
        elif venue == "away":
            mask = normalized_away == team
        else:
            mask = (normalized_home == team) | (normalized_away == team)
        h = hist[mask & hist.home_score.notna() & hist.away_score.notna()].tail(10)
        rows = []
        for _, r in h.iterrows():
            row_home = normalize_team_name(str(r.home_team), "soccer")
            if row_home == team:
                result = "W" if r.home_score > r.away_score else "D" if r.home_score == r.away_score else "L"
                rows.append({"gf": r.home_score, "ga": r.away_score, "gd": r.home_score - r.away_score, "points": 3 if result == "W" else 1 if result == "D" else 0, "result": result})
            else:
                result = "W" if r.away_score > r.home_score else "D" if r.home_score == r.away_score else "L"
                rows.append({"gf": r.away_score, "ga": r.home_score, "gd": r.away_score - r.home_score, "points": 3 if result == "W" else 1 if result == "D" else 0, "result": result})
        return rows

    def elo_before_fixture() -> tuple[float, float]:
        ratings: dict[str, float] = {}
        if hist.empty:
            return 1500.0, 1500.0
        for _, r in hist.iterrows():
            if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
                continue
            h = normalize_team_name(str(r.home_team), "soccer")
            a = normalize_team_name(str(r.away_team), "soccer")
            hr, ar = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
            score_h = 1.0 if r.home_score > r.away_score else 0.5 if r.home_score == r.away_score else 0.0
            ratings[h], ratings[a] = _update_elo(hr, ar, score_h)
        return ratings.get(home_team, 1500.0), ratings.get(away_team, 1500.0)

    def avg(rows, key, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(float(x[key]) for x in sample) / len(sample) if sample else default

    def rate(rows, key, value, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(1 for x in sample if x[key] == value) / len(sample) if sample else default

    def condition(rows, predicate, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(1 for x in sample if predicate(x)) / len(sample) if sample else default

    hh, ah = recent(home_team), recent(away_team)
    h_home, a_away = recent(home_team, "home"), recent(away_team, "away")
    league_difficulty = _soccer_league_difficulty(league)
    home_elo, away_elo = elo_before_fixture()
    normalized_home_elo = home_elo * league_difficulty
    normalized_away_elo = away_elo * league_difficulty
    return {
        "home_form_points": avg(hh, "points", 1.2),
        "away_form_points": avg(ah, "points", 1.2),
        "home_form_points_3": avg(hh, "points", 1.2, 3),
        "away_form_points_3": avg(ah, "points", 1.2, 3),
        "home_form_points_5": avg(hh, "points", 1.2, 5),
        "away_form_points_5": avg(ah, "points", 1.2, 5),
        "home_win_rate_5": rate(hh, "result", "W", 0.38, 5),
        "away_win_rate_5": rate(ah, "result", "W", 0.32, 5),
        "home_draw_rate_5": rate(hh, "result", "D", 0.28, 5),
        "away_draw_rate_5": rate(ah, "result", "D", 0.28, 5),
        "home_loss_rate_5": rate(hh, "result", "L", 0.34, 5),
        "away_loss_rate_5": rate(ah, "result", "L", 0.40, 5),
        "home_goals_for": avg(hh, "gf", 1.3),
        "home_goals_against": avg(hh, "ga", 1.2),
        "away_goals_for": avg(ah, "gf", 1.1),
        "away_goals_against": avg(ah, "ga", 1.3),
        "home_home_goals_for": avg(h_home, "gf", 1.4),
        "home_home_goals_against": avg(h_home, "ga", 1.1),
        "away_away_goals_for": avg(a_away, "gf", 1.0),
        "away_away_goals_against": avg(a_away, "ga", 1.4),
        "home_goal_diff": avg(hh, "gd", 0.1),
        "away_goal_diff": avg(ah, "gd", -0.1),
        "home_clean_sheet_rate_5": condition(hh, lambda x: x["ga"] == 0, 0.28, 5),
        "away_clean_sheet_rate_5": condition(ah, lambda x: x["ga"] == 0, 0.22, 5),
        "home_failed_score_rate_5": condition(hh, lambda x: x["gf"] == 0, 0.25, 5),
        "away_failed_score_rate_5": condition(ah, lambda x: x["gf"] == 0, 0.30, 5),
        "home_unbeaten_rate_10": condition(hh, lambda x: x["result"] != "L", 0.62, 10),
        "away_unbeaten_rate_10": condition(ah, lambda x: x["result"] != "L", 0.55, 10),
        "home_elo": normalized_home_elo,
        "away_elo": normalized_away_elo,
        "elo_diff": normalized_home_elo - normalized_away_elo,
        "league_strength": league_difficulty,
        "home_implied": 1 / home_odds if home_odds else 0.0,
        "draw_implied": 1 / draw_odds if draw_odds else 0.0,
        "away_implied": 1 / away_odds if away_odds else 0.0,
        "odds_margin": _odds_margin(home_odds, draw_odds, away_odds),
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


def basketball_features_for_fixture(history: pd.DataFrame, home_team: str, away_team: str, fixture_date=None) -> dict:
    home_team = normalize_team_name(home_team, "basketball")
    away_team = normalize_team_name(away_team, "basketball")
    hist = history[history.get("sport", "basketball") == "basketball"].sort_values("match_date") if not history.empty else history
    cutoff = pd.to_datetime(fixture_date, errors="coerce") if fixture_date is not None else None
    if not hist.empty and cutoff is not None and not pd.isna(cutoff):
        hist = hist[pd.to_datetime(hist["match_date"], errors="coerce") < cutoff]

    def recent(team):
        if hist.empty:
            return []
        normalized_home = hist.home_team.map(lambda x: normalize_team_name(str(x), "basketball"))
        normalized_away = hist.away_team.map(lambda x: normalize_team_name(str(x), "basketball"))
        h = hist[((normalized_home == team) | (normalized_away == team)) & hist.home_score.notna() & hist.away_score.notna()].tail(10)
        rows = []
        for _, r in h.iterrows():
            row_home = normalize_team_name(str(r.home_team), "basketball")
            pf, pa = (r.home_score, r.away_score) if row_home == team else (r.away_score, r.home_score)
            rows.append((pf, pa, pf - pa, 1 if pf > pa else 0))
        return rows

    def elo_and_rest() -> tuple[float, float, int, int]:
        ratings: dict[str, float] = {}
        last_played: dict[str, pd.Timestamp] = {}
        if hist.empty:
            return 1500.0, 1500.0, 3, 3
        for _, r in hist.iterrows():
            if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
                continue
            h = normalize_team_name(str(r.home_team), "basketball")
            a = normalize_team_name(str(r.away_team), "basketball")
            hr, ar = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
            score_h = 1.0 if r.home_score > r.away_score else 0.0
            ratings[h], ratings[a] = _update_elo(hr, ar, score_h, k=20)
            game_date = pd.to_datetime(r.get("match_date"), errors="coerce")
            if not pd.isna(game_date):
                last_played[h] = game_date
                last_played[a] = game_date
        reference_date = cutoff if cutoff is not None and not pd.isna(cutoff) else pd.Timestamp.utcnow().tz_localize(None)
        home_rest = (reference_date - last_played[home_team]).days if home_team in last_played else 3
        away_rest = (reference_date - last_played[away_team]).days if away_team in last_played else 3
        return ratings.get(home_team, 1500.0), ratings.get(away_team, 1500.0), min(max(home_rest, 0), 7), min(max(away_rest, 0), 7)

    def avg(rows, idx, default):
        return sum(x[idx] for x in rows) / len(rows) if rows else default

    hh, ah = recent(home_team), recent(away_team)
    home_elo, away_elo, home_rest, away_rest = elo_and_rest()
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
        "home_elo": home_elo,
        "away_elo": away_elo,
        "elo_diff": home_elo - away_elo,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "home_back_to_back": 1 if home_rest <= 1 else 0,
        "away_back_to_back": 1 if away_rest <= 1 else 0,
    }
