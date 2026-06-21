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


def _ema_dict(hist: list[dict], key: str, default: float, window: int = 5, alpha: float | None = None) -> float:
    """Exponential moving average — recent matches weighted more heavily.

    alpha defaults to 2/(window+1) (standard EMA span).
    Higher alpha = more weight on recent games.
    """
    rows = hist[-window * 2:] if hist else []  # look back 2× window for stability
    if not rows:
        return default
    a = alpha if alpha else 2.0 / (window + 1)
    ema = float(rows[0].get(key, default))
    for r in rows[1:]:
        v = float(r.get(key, default))
        ema = a * v + (1 - a) * ema
    return round(ema, 4)


def _elo_expected(a: float, b: float) -> float:
    return 1 / (1 + 10 ** ((b - a) / 400))


def _update_elo(a: float, b: float, score_a: float, k: float = 24) -> tuple[float, float]:
    expected_a = _elo_expected(a, b)
    expected_b = 1 - expected_a
    return a + k * (score_a - expected_a), b + k * ((1 - score_a) - expected_b)


# --- H2H tracker — venue-aware ---
def _update_h2h(h2h: dict, home: str, away: str, hs: int, aas: int) -> None:
    """Track H2H results including venue-specific outcomes."""
    key = tuple(sorted((home, away)))
    if key not in h2h:
        h2h[key] = {
            "home_wins": 0, "away_wins": 0, "draws": 0, "total": 0,
            "home_goals": 0, "away_goals": 0,
            # Venue-specific: when 'home' was actually at home
            "venue_home_wins": 0, "venue_away_wins": 0, "venue_draws": 0, "venue_total": 0,
            # Last 3 meetings
            "last3_home_goals": [], "last3_away_goals": [],
        }
    r = h2h[key]
    r["total"] += 1
    if hs > aas:
        r["home_wins" if key[0] == home else "away_wins"] += 1
    elif hs == aas:
        r["draws"] += 1
    r["home_goals"] += hs
    r["away_goals"] += aas
    # Venue tracking (home = actual home team)
    r["venue_total"] += 1
    if hs > aas:
        r["venue_home_wins"] += 1
    elif hs == aas:
        r["venue_draws"] += 1
    else:
        r["venue_away_wins"] += 1
    # Rolling last-3
    r["last3_home_goals"].append(hs)
    r["last3_away_goals"].append(aas)
    if len(r["last3_home_goals"]) > 3:
        r["last3_home_goals"] = r["last3_home_goals"][-3:]
        r["last3_away_goals"] = r["last3_away_goals"][-3:]


def _h2h_features(h2h: dict, home: str, away: str) -> dict:
    key = tuple(sorted((home, away)))
    record = h2h.get(key)
    if not record or record["total"] < 2:
        return {
            "h2h_home_win_rate": 0.50,
            "h2h_draw_rate": 0.25,
            "h2h_away_win_rate": 0.25,
            "h2h_avg_total_goals": 2.5,
        }
    total = record["total"]
    home_wins = record["home_wins"] if key[0] == home else record["away_wins"]
    away_wins = record["away_wins"] if key[0] == home else record["home_wins"]
    # Venue-adjusted rates
    vt = record.get("venue_total", 0)
    h_venue_wr = record.get("venue_home_wins", 0) / vt if vt >= 2 else 0.50
    a_venue_wr = record.get("venue_away_wins", 0) / vt if vt >= 2 else 0.25
    # Last-3 average goals
    l3h = record.get("last3_home_goals", [])
    l3a = record.get("last3_away_goals", [])
    l3h_avg = sum(l3h) / len(l3h) if l3h else 1.3
    l3a_avg = sum(l3a) / len(l3a) if l3a else 1.1
    return {
        "h2h_home_win_rate":       home_wins / total,
        "h2h_draw_rate":           record["draws"] / total,
        "h2h_away_win_rate":       away_wins / total,
        "h2h_avg_total_goals":     (record["home_goals"] + record["away_goals"]) / total,
        "h2h_home_venue_win_rate": h_venue_wr,
        "h2h_away_venue_win_rate": a_venue_wr,
        "h2h_last3_home_goals":    l3h_avg,
        "h2h_last3_away_goals":    l3a_avg,
    }


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
    "SERIE B": 0.78,
    "LIGA PORTUGAL": 0.82,
    "PRIMEIRA LIGA": 0.82,
    "LA LIGA 2": 0.74,
    "BUNDESLIGA 2": 0.76,
    "LIGUE 2": 0.72,
    "MLS": 0.74,
    "J LEAGUE": 0.70,
    "A LEAGUE": 0.68,
    "SAUDI LEAGUE": 0.72,
    "CHINA SUPER LEAGUE": 0.66,
}


def _soccer_league_difficulty(league) -> float:
    return SOCCER_LEAGUE_DIFFICULTY.get(str(league), 0.75)


def _odds_margin(home_odds, draw_odds, away_odds) -> float:
    try:
        if not home_odds or not draw_odds or not away_odds:
            return 0.0
        return max((1 / float(home_odds)) + (1 / float(draw_odds)) + (1 / float(away_odds)) - 1, 0.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _implied_prob(odds) -> float:
    try:
        return 1 / float(odds) if odds else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def normalize_result(home_score: int, away_score: int) -> int:
    if home_score > away_score:
        return 2
    if home_score == away_score:
        return 1
    return 0


def build_soccer_features(fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    if "sport" in fixtures.columns:
        df = fixtures[fixtures["sport"] == "soccer"].sort_values("match_date").copy()
    else:
        df = fixtures.sort_values("match_date").copy()
    rows, y = [], []
    team_hist: dict[str, list[dict]] = {}
    team_home_hist: dict[str, list[dict]] = {}
    team_away_hist: dict[str, list[dict]] = {}
    team_elo: dict[str, float] = {}
    team_elo_home: dict[str, float] = {}  # separate home/away Elo
    team_elo_away: dict[str, float] = {}
    streak_tracker: dict[str, list[str]] = {}  # track result streaks
    h2h_tracker: dict = {}
    season_form: dict[str, list[float]] = {}  # points per match in season
    for _, r in df.iterrows():
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        home = normalize_team_name(r["home_team"], "soccer")
        away = normalize_team_name(r["away_team"], "soccer")
        hh, ah = team_hist.get(home, [])[-10:], team_hist.get(away, [])[-10:]
        h_home, a_away = team_home_hist.get(home, [])[-10:], team_away_hist.get(away, [])[-10:]
        home_elo, away_elo = team_elo.get(home, 1500.0), team_elo.get(away, 1500.0)
        home_elo_h, away_elo_a = team_elo_home.get(home, 1500.0), team_elo_away.get(away, 1500.0)
        league_difficulty = _soccer_league_difficulty(r.get("league"))
        normalized_home_elo = home_elo * league_difficulty
        normalized_away_elo = away_elo * league_difficulty
        hs, aas = int(r["home_score"]), int(r["away_score"])
        home_result = 1.0 if hs > aas else 0.5 if hs == aas else 0.0

        # --- Streak tracking ---
        home_streak = streak_tracker.get(home, [])
        away_streak = streak_tracker.get(away, [])
        home_current_streak_len = 0
        if home_streak:
            last_result = home_streak[-1]
            for res in reversed(home_streak):
                if res == last_result:
                    home_current_streak_len += 1
                else:
                    break
        away_current_streak_len = 0
        if away_streak:
            last_result = away_streak[-1]
            for res in reversed(away_streak):
                if res == last_result:
                    away_current_streak_len += 1
                else:
                    break
        home_streak_type = home_streak[-1] if home_streak else "N"
        away_streak_type = away_streak[-1] if away_streak else "N"

        # --- Season form (relative to league avg) ---
        home_season_pts = season_form.get(home, [])
        away_season_pts = season_form.get(away, [])
        home_season_avg = sum(home_season_pts) / len(home_season_pts) if home_season_pts else 1.2
        away_season_avg = sum(away_season_pts) / len(away_season_pts) if away_season_pts else 1.2

        # --- H2H features ---
        h2h_feats = _h2h_features(h2h_tracker, home, away)

        rows.append({
            # --- Core form ---
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
            # --- EMA-weighted form (recent matches matter more) ---
            "home_ema_form_5":  _ema_dict(hh, "points", 1.2, window=5),
            "away_ema_form_5":  _ema_dict(ah, "points", 1.2, window=5),
            "home_ema_form_10": _ema_dict(hh, "points", 1.2, window=10),
            "away_ema_form_10": _ema_dict(ah, "points", 1.2, window=10),
            "home_ema_goals_for":     _ema_dict(hh, "gf", 1.3, window=5),
            "away_ema_goals_for":     _ema_dict(ah, "gf", 1.1, window=5),
            "home_ema_goals_against": _ema_dict(hh, "ga", 1.2, window=5),
            "away_ema_goals_against": _ema_dict(ah, "ga", 1.3, window=5),
            # --- Scoring / conceding ---
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
            # --- Elo ---
            "home_elo": normalized_home_elo,
            "away_elo": normalized_away_elo,
            "elo_diff": normalized_home_elo - normalized_away_elo,
            "home_elo_home_only": home_elo_h * league_difficulty,
            "away_elo_away_only": away_elo_a * league_difficulty,
            "elo_diff_venue": (home_elo_h - away_elo_a) * league_difficulty,
            # --- League / season context ---
            "league_strength": league_difficulty,
            "home_form_vs_season": home_season_avg - 1.2,
            "away_form_vs_season": away_season_avg - 1.2,
            # --- Streaks ---
            "home_streak_len": min(home_current_streak_len, 10),
            "away_streak_len": min(away_current_streak_len, 10),
            "home_streak_winning": 1 if home_streak_type == "W" else 0,
            "away_streak_winning": 1 if away_streak_type == "W" else 0,
            "home_streak_losing": 1 if home_streak_type == "L" else 0,
            "away_streak_losing": 1 if away_streak_type == "L" else 0,
            # --- H2H (standard + venue-adjusted) ---
            "h2h_home_win_rate":       h2h_feats["h2h_home_win_rate"],
            "h2h_draw_rate":           h2h_feats["h2h_draw_rate"],
            "h2h_away_win_rate":       h2h_feats["h2h_away_win_rate"],
            "h2h_avg_total_goals":     h2h_feats["h2h_avg_total_goals"],
            "h2h_home_venue_win_rate": h2h_feats.get("h2h_home_venue_win_rate", 0.50),
            "h2h_away_venue_win_rate": h2h_feats.get("h2h_away_venue_win_rate", 0.25),
            "h2h_last3_home_goals":    h2h_feats.get("h2h_last3_home_goals", 1.3),
            "h2h_last3_away_goals":    h2h_feats.get("h2h_last3_away_goals", 1.1),
            # --- Consistency ---
            "home_scoring_consistency": _condition_rate(hh, lambda x: x["gf"] >= 1, 0.65, 5),
            "away_scoring_consistency": _condition_rate(ah, lambda x: x["gf"] >= 1, 0.60, 5),
            "home_conceding_consistency": _condition_rate(hh, lambda x: x["ga"] >= 1, 0.55, 5),
            "away_conceding_consistency": _condition_rate(ah, lambda x: x["ga"] >= 1, 0.60, 5),
            # --- Last match ---
            "last_match_home_goals": hh[-1]["gf"] if hh else 1.3,
            "last_match_away_goals": ah[-1]["gf"] if ah else 1.1,
            "last_match_home_conceded": hh[-1]["ga"] if hh else 1.2,
            "last_match_away_conceded": ah[-1]["ga"] if ah else 1.3,
            # --- Momentum ---
            "home_goal_diff_momentum": (
                sum(x["gd"] for x in hh[-3:]) / 3 if len(hh) >= 3
                else sum(x["gd"] for x in hh) / max(len(hh), 1)
            ),
            "away_goal_diff_momentum": (
                sum(x["gd"] for x in ah[-3:]) / 3 if len(ah) >= 3
                else sum(x["gd"] for x in ah) / max(len(ah), 1)
            ),
            # --- Rest days (days since last match — proxy from row index gap) ---
            "home_rest_days": 4.0,   # populated in features_for_fixture; default to avg
            "away_rest_days": 4.0,
            "home_back_to_back": 0,
            "away_back_to_back": 0,
            "rest_advantage": 0.0,
            # --- Market signals ---
            "home_implied": _implied_prob(r.get("home_odds")),
            "draw_implied": _implied_prob(r.get("draw_odds")),
            "away_implied": _implied_prob(r.get("away_odds")),
            "odds_margin": _odds_margin(r.get("home_odds"), r.get("draw_odds"), r.get("away_odds")),
            "sharp_home_move": 0.0,   # populated from odds snapshot history in features_for_fixture
            "sharp_away_move": 0.0,
            "clv_home_signal": 0.0,
            # --- Set-piece / high-scoring proxy ---
            "home_high_scoring_rate": _condition_rate(hh, lambda x: (x["gf"] + x["ga"]) >= 3, 0.45, 10),
            "away_high_scoring_rate": _condition_rate(ah, lambda x: (x["gf"] + x["ga"]) >= 3, 0.45, 10),
        })
        y.append(normalize_result(hs, aas))
        home_entry = {"gf": hs, "ga": aas, "gd": hs - aas, "points": 3 if hs > aas else 1 if hs == aas else 0, "result": "W" if hs > aas else "D" if hs == aas else "L"}
        away_entry = {"gf": aas, "ga": hs, "gd": aas - hs, "points": 3 if aas > hs else 1 if hs == aas else 0, "result": "W" if aas > hs else "D" if hs == aas else "L"}
        team_hist.setdefault(home, []).append(home_entry)
        team_hist.setdefault(away, []).append(away_entry)
        team_home_hist.setdefault(home, []).append(home_entry)
        team_away_hist.setdefault(away, []).append(away_entry)
        team_elo[home], team_elo[away] = _update_elo(home_elo, away_elo, home_result)
        # Separate home/away Elo
        team_elo_home[home] = _update_elo(home_elo_h, away_elo, 1.0, k=24)[0]  # home win/loss
        team_elo_away[away] = _update_elo(away_elo_a, home_elo, 1.0 - home_result, k=24)[0]
        # Streak tracking
        home_result_label = "W" if hs > aas else "D" if hs == aas else "L"
        away_result_label = "W" if aas > hs else "D" if hs == aas else "L"
        streak_tracker.setdefault(home, []).append(home_result_label)
        streak_tracker.setdefault(away, []).append(away_result_label)
        # Season form
        season_form.setdefault(home, []).append(3 if hs > aas else 1 if hs == aas else 0)
        season_form.setdefault(away, []).append(3 if aas > hs else 1 if hs == aas else 0)
        # H2H tracking
        _update_h2h(h2h_tracker, home, away, hs, aas)
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
    insider: dict | None = None,
) -> dict:
    home_team = normalize_team_name(home_team, "soccer")
    away_team = normalize_team_name(away_team, "soccer")
    if not history.empty and "sport" in history.columns:
        hist = history[history["sport"] == "soccer"].sort_values("match_date")
    elif not history.empty:
        hist = history.sort_values("match_date")
    else:
        hist = history
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

    def elo_before_fixture() -> tuple[float, float, float, float]:
        ratings: dict[str, float] = {}
        ratings_home: dict[str, float] = {}
        ratings_away: dict[str, float] = {}
        if hist.empty:
            return 1500.0, 1500.0, 1500.0, 1500.0
        for _, r in hist.iterrows():
            if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
                continue
            h = normalize_team_name(str(r.home_team), "soccer")
            a = normalize_team_name(str(r.away_team), "soccer")
            hr, ar = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
            score_h = 1.0 if r.home_score > r.away_score else 0.5 if r.home_score == r.away_score else 0.0
            ratings[h], ratings[a] = _update_elo(hr, ar, score_h)
            # Home/away-specific Elo
            hhr = ratings_home.get(h, 1500.0)
            aar = ratings_away.get(a, 1500.0)
            ratings_home[h], ratings_away[a] = _update_elo(hhr, ar, score_h, k=24)
        return ratings.get(home_team, 1500.0), ratings.get(away_team, 1500.0), ratings_home.get(home_team, 1500.0), ratings_away.get(away_team, 1500.0)

    def avg(rows, key, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(float(x[key]) for x in sample) / len(sample) if sample else default

    def rate(rows, key, value, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(1 for x in sample if x[key] == value) / len(sample) if sample else default

    def condition(rows, predicate, default, window: int | None = None):
        sample = rows[-window:] if window else rows
        return sum(1 for x in sample if predicate(x)) / len(sample) if sample else default

    # --- H2H computation ---
    def compute_h2h() -> dict:
        h2h: dict = {}
        if hist.empty:
            return {"h2h_home_win_rate": 0.50, "h2h_draw_rate": 0.25, "h2h_away_win_rate": 0.25, "h2h_avg_total_goals": 2.5}
        for _, r in hist.iterrows():
            if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
                continue
            h = normalize_team_name(str(r.home_team), "soccer")
            a = normalize_team_name(str(r.away_team), "soccer")
            _update_h2h(h2h, h, a, int(r["home_score"]), int(r["away_score"]))
        return _h2h_features(h2h, home_team, away_team)

    # --- Streak computation ---
    def compute_streaks(rows_list):
        if not rows_list:
            return 0, "N"
        last = rows_list[-1]["result"]
        length = 0
        for r in reversed(rows_list):
            if r["result"] == last:
                length += 1
            else:
                break
        return length, last

    # --- Season form ---
    def compute_season_avg(rows_list):
        pts = [r["points"] for r in rows_list]
        return sum(pts) / len(pts) if pts else 1.2

    hh, ah = recent(home_team), recent(away_team)
    h_home, a_away = recent(home_team, "home"), recent(away_team, "away")
    league_difficulty = _soccer_league_difficulty(league)
    home_elo, away_elo, home_elo_h, away_elo_a = elo_before_fixture()
    normalized_home_elo = home_elo * league_difficulty
    normalized_away_elo = away_elo * league_difficulty
    h2h_feats = compute_h2h()
    home_streak_len, home_streak_type = compute_streaks(hh)
    away_streak_len, away_streak_type = compute_streaks(ah)
    home_season_avg = compute_season_avg(hh)
    away_season_avg = compute_season_avg(ah)

    # Rest days — days since last match for each team
    def _rest_days(team: str) -> float:
        if hist.empty or fixture_date is None:
            return 4.0
        hn = normalize_team_name(team, "soccer")
        norm_home = hist.home_team.map(lambda x: normalize_team_name(str(x), "soccer"))
        norm_away = hist.away_team.map(lambda x: normalize_team_name(str(x), "soccer"))
        mask = (norm_home == hn) | (norm_away == hn)
        team_games = hist[mask].copy()
        if team_games.empty:
            return 4.0
        last_date = pd.to_datetime(team_games["match_date"].max(), errors="coerce")
        cutoff = pd.to_datetime(fixture_date, errors="coerce")
        if pd.isna(last_date) or pd.isna(cutoff):
            return 4.0
        return min(float((cutoff - last_date).days), 14.0)

    home_rest = _rest_days(home_team)
    away_rest = _rest_days(away_team)

    return {
        # --- Core form ---
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
        # --- EMA form ---
        "home_ema_form_5":  _ema_dict(hh, "points", 1.2, window=5),
        "away_ema_form_5":  _ema_dict(ah, "points", 1.2, window=5),
        "home_ema_form_10": _ema_dict(hh, "points", 1.2, window=10),
        "away_ema_form_10": _ema_dict(ah, "points", 1.2, window=10),
        "home_ema_goals_for":     _ema_dict(hh, "gf", 1.3, window=5),
        "away_ema_goals_for":     _ema_dict(ah, "gf", 1.1, window=5),
        "home_ema_goals_against": _ema_dict(hh, "ga", 1.2, window=5),
        "away_ema_goals_against": _ema_dict(ah, "ga", 1.3, window=5),
        # --- Scoring / conceding ---
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
        # --- Elo ---
        "home_elo": normalized_home_elo,
        "away_elo": normalized_away_elo,
        "elo_diff": normalized_home_elo - normalized_away_elo,
        "home_elo_home_only": home_elo_h * league_difficulty,
        "away_elo_away_only": away_elo_a * league_difficulty,
        "elo_diff_venue": (home_elo_h - away_elo_a) * league_difficulty,
        # --- League / season ---
        "league_strength": league_difficulty,
        "home_form_vs_season": home_season_avg - 1.2,
        "away_form_vs_season": away_season_avg - 1.2,
        # --- Streaks ---
        "home_streak_len": min(home_streak_len, 10),
        "away_streak_len": min(away_streak_len, 10),
        "home_streak_winning": 1 if home_streak_type == "W" else 0,
        "away_streak_winning": 1 if away_streak_type == "W" else 0,
        "home_streak_losing": 1 if home_streak_type == "L" else 0,
        "away_streak_losing": 1 if away_streak_type == "L" else 0,
        # --- H2H (standard + venue) ---
        "h2h_home_win_rate":       h2h_feats.get("h2h_home_win_rate", 0.50),
        "h2h_draw_rate":           h2h_feats.get("h2h_draw_rate", 0.25),
        "h2h_away_win_rate":       h2h_feats.get("h2h_away_win_rate", 0.25),
        "h2h_avg_total_goals":     h2h_feats.get("h2h_avg_total_goals", 2.5),
        "h2h_home_venue_win_rate": h2h_feats.get("h2h_home_venue_win_rate", 0.50),
        "h2h_away_venue_win_rate": h2h_feats.get("h2h_away_venue_win_rate", 0.25),
        "h2h_last3_home_goals":    h2h_feats.get("h2h_last3_home_goals", 1.3),
        "h2h_last3_away_goals":    h2h_feats.get("h2h_last3_away_goals", 1.1),
        # --- Consistency ---
        "home_scoring_consistency": condition(hh, lambda x: x["gf"] >= 1, 0.65, 5),
        "away_scoring_consistency": condition(ah, lambda x: x["gf"] >= 1, 0.60, 5),
        "home_conceding_consistency": condition(hh, lambda x: x["ga"] >= 1, 0.55, 5),
        "away_conceding_consistency": condition(ah, lambda x: x["ga"] >= 1, 0.60, 5),
        # --- Last match ---
        "last_match_home_goals": hh[-1]["gf"] if hh else 1.3,
        "last_match_away_goals": ah[-1]["gf"] if ah else 1.1,
        "last_match_home_conceded": hh[-1]["ga"] if hh else 1.2,
        "last_match_away_conceded": ah[-1]["ga"] if ah else 1.3,
        # --- Momentum ---
        "home_goal_diff_momentum": (
            sum(x["gd"] for x in hh[-3:]) / 3 if len(hh) >= 3
            else sum(x["gd"] for x in hh) / max(len(hh), 1)
        ),
        "away_goal_diff_momentum": (
            sum(x["gd"] for x in ah[-3:]) / 3 if len(ah) >= 3
            else sum(x["gd"] for x in ah) / max(len(ah), 1)
        ),
        # --- Rest days / fatigue ---
        "home_rest_days":    home_rest,
        "away_rest_days":    away_rest,
        "home_back_to_back": 1 if home_rest <= 2 else 0,
        "away_back_to_back": 1 if away_rest <= 2 else 0,
        "rest_advantage":    round(home_rest - away_rest, 1),
        # --- Market signals ---
        "home_implied":   _implied_prob(home_odds),
        "draw_implied":   _implied_prob(draw_odds),
        "away_implied":   _implied_prob(away_odds),
        "odds_margin":    _odds_margin(home_odds, draw_odds, away_odds),
        "sharp_home_move": 0.0,
        "sharp_away_move": 0.0,
        "clv_home_signal": 0.0,
        # --- Set-piece proxy ---
        "home_high_scoring_rate": condition(hh, lambda x: (x["gf"] + x["ga"]) >= 3, 0.45, 10),
        "away_high_scoring_rate": condition(ah, lambda x: (x["gf"] + x["ga"]) >= 3, 0.45, 10),
        # --- Insider signals (sharp money / weather / injuries / referee) ---
        "insider_sharp_home_move": float((insider or {}).get("insider_sharp_home_move", 0.0)),
        "insider_sharp_away_move": float((insider or {}).get("insider_sharp_away_move", 0.0)),
        "insider_clv_home":        float((insider or {}).get("insider_clv_home",        0.0)),
        "insider_steam":           float((insider or {}).get("insider_steam",           0.0)),
        "insider_opening_home_prob": float((insider or {}).get("insider_opening_home_prob", 0.0)),
        "insider_weather_precip":  float((insider or {}).get("insider_weather_precip",  0.0)),
        "insider_weather_wind":    float((insider or {}).get("insider_weather_wind",    0.0)),
        "insider_home_injury":     float((insider or {}).get("insider_home_injury",     0.0)),
        "insider_away_injury":     float((insider or {}).get("insider_away_injury",     0.0)),
        "insider_referee_cards":   float((insider or {}).get("insider_referee_cards",   3.0)),
        "insider_public_home_pct": float((insider or {}).get("insider_public_home_pct", 50.0)),
    }


def build_basketball_features(fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    if "sport" in fixtures.columns:
        df = fixtures[fixtures["sport"] == "basketball"].sort_values("match_date").copy()
    else:
        df = fixtures.sort_values("match_date").copy()
    rows, y = [], []
    team_hist: dict[str, list[dict]] = {}
    team_elo: dict[str, float] = {}
    last_played: dict[str, pd.Timestamp] = {}
    streak_tracker: dict[str, list[str]] = {}
    h2h_tracker: dict = {}
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

        # Streaks
        home_streak = streak_tracker.get(home, [])
        away_streak = streak_tracker.get(away, [])
        home_current_streak_len = 0
        if home_streak:
            last_res = home_streak[-1]
            for res in reversed(home_streak):
                if res == last_res:
                    home_current_streak_len += 1
                else:
                    break
        away_current_streak_len = 0
        if away_streak:
            last_res = away_streak[-1]
            for res in reversed(away_streak):
                if res == last_res:
                    away_current_streak_len += 1
                else:
                    break
        home_streak_type = home_streak[-1] if home_streak else "N"
        away_streak_type = away_streak[-1] if away_streak else "N"

        # H2H
        key = tuple(sorted((home, away)))
        h2h_rec = h2h_tracker.get(key)
        h2h_home_advantage = 0.50
        if h2h_rec and h2h_rec["total"] >= 2:
            h2h_home_wins = h2h_rec["home_wins"] if key[0] == home else h2h_rec["away_wins"]
            h2h_home_advantage = h2h_home_wins / h2h_rec["total"]

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

            # --- NEW FEATURES ---
            "home_streak_len": min(home_current_streak_len, 10),
            "away_streak_len": min(away_current_streak_len, 10),
            "home_streak_wins": 1 if home_streak_type == "W" else 0,
            "away_streak_wins": 1 if away_streak_type == "W" else 0,
            "h2h_home_advantage": h2h_home_advantage,
            "home_scoring_volatility": _avg_dict(hh, "margin_sq", 25),  # variance proxy
            "away_scoring_volatility": _avg_dict(ah, "margin_sq", 25),
            "home_avg_margin_last_3": _avg_dict(hh, "margin", 2, 3),
            "away_avg_margin_last_3": _avg_dict(ah, "margin", -2, 3),
        })
        hs, aas = int(r["home_score"]), int(r["away_score"])
        y.append(1 if hs > aas else 0)
        margin = hs - aas
        home_entry = {"pf": hs, "pa": aas, "margin": margin, "margin_sq": margin ** 2, "win": 1 if hs > aas else 0}
        away_entry = {"pf": aas, "pa": hs, "margin": aas - hs, "margin_sq": (aas - hs) ** 2, "win": 1 if aas > hs else 0}
        team_hist.setdefault(home, []).append(home_entry)
        team_hist.setdefault(away, []).append(away_entry)
        team_elo[home], team_elo[away] = _update_elo(home_elo, away_elo, 1.0 if hs > aas else 0.0, k=20)
        if not pd.isna(game_date):
            last_played[home] = game_date
            last_played[away] = game_date
        # Streak
        h_res = "W" if hs > aas else "L"
        a_res = "W" if aas > hs else "L"
        streak_tracker.setdefault(home, []).append(h_res)
        streak_tracker.setdefault(away, []).append(a_res)
        # H2H
        _update_h2h(h2h_tracker, home, away, hs, aas)
    return pd.DataFrame(rows).fillna(0), pd.Series(y)


def basketball_features_for_fixture(history: pd.DataFrame, home_team: str, away_team: str, fixture_date=None) -> dict:
    home_team = normalize_team_name(home_team, "basketball")
    away_team = normalize_team_name(away_team, "basketball")
    if not history.empty and "sport" in history.columns:
        hist = history[history["sport"] == "basketball"].sort_values("match_date")
    elif not history.empty:
        hist = history.sort_values("match_date")
    else:
        hist = history
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

    # Streaks
    def compute_streaks(rows_list):
        if not rows_list:
            return 0, "N"
        last = "W" if rows_list[-1][3] == 1 else "L"
        length = 0
        for r in reversed(rows_list):
            res = "W" if r[3] == 1 else "L"
            if res == last:
                length += 1
            else:
                break
        return length, last

    hh, ah = recent(home_team), recent(away_team)
    home_elo, away_elo, home_rest, away_rest = elo_and_rest()
    home_streak_len, home_streak_type = compute_streaks(hh)
    away_streak_len, away_streak_type = compute_streaks(ah)

    # H2H
    h2h: dict = {}
    if not hist.empty:
        for _, r in hist.iterrows():
            if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
                continue
            h = normalize_team_name(str(r.home_team), "basketball")
            a = normalize_team_name(str(r.away_team), "basketball")
            _update_h2h(h2h, h, a, int(r["home_score"]), int(r["away_score"]))
    key = tuple(sorted((home_team, away_team)))
    h2h_rec = h2h.get(key)
    h2h_home_advantage = 0.50
    if h2h_rec and h2h_rec["total"] >= 2:
        h2h_home_wins = h2h_rec["home_wins"] if key[0] == home_team else h2h_rec["away_wins"]
        h2h_home_advantage = h2h_home_wins / h2h_rec["total"]

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

        # --- NEW FEATURES ---
        "home_streak_len": min(home_streak_len, 10),
        "away_streak_len": min(away_streak_len, 10),
        "home_streak_wins": 1 if home_streak_type == "W" else 0,
        "away_streak_wins": 1 if away_streak_type == "W" else 0,
        "h2h_home_advantage": h2h_home_advantage,
        "home_avg_margin_last_3": avg(hh[-3:], 2, 2) if len(hh) >= 3 else avg(hh, 2, 2),
        "away_avg_margin_last_3": avg(ah[-3:], 2, -2) if len(ah) >= 3 else avg(ah, 2, -2),
    }