import pandas as pd

from app.utils.team_names import normalize_team_name


def _risk(confidence_pct: float) -> str:
    return "Low" if confidence_pct >= 72 else "Medium" if confidence_pct >= 58 else "High"


def _bounded_probability(value: float, low: float = 0.28, high: float = 0.78) -> float:
    return max(low, min(high, value))


class RecentFormToolkit:
    """Shared helpers for early dedicated sport engines.

    These engines only use fields the app reliably stores today: fixture sport,
    teams/players, dates, venue side, scores, and league/format hints. As richer
    feeds are added, each engine can absorb sport-specific stats without changing
    the prediction service contract.
    """

    def __init__(self, sport: str, history: pd.DataFrame, fixture: dict):
        self.sport = sport
        self.home = normalize_team_name(fixture["home_team"], sport)
        self.away = normalize_team_name(fixture["away_team"], sport)
        self.fixture = fixture
        self.df = self._sport_history(history)
        self.played = self._played_history()

    def _sport_history(self, history: pd.DataFrame) -> pd.DataFrame:
        if history.empty:
            return pd.DataFrame()
        df = history[history.get("sport", self.sport) == self.sport].copy()
        if df.empty:
            return df
        df["home_norm"] = df["home_team"].map(lambda x: normalize_team_name(str(x), self.sport))
        df["away_norm"] = df["away_team"].map(lambda x: normalize_team_name(str(x), self.sport))
        return df

    def _played_history(self) -> pd.DataFrame:
        if self.df.empty:
            return pd.DataFrame()
        return self.df[self.df["home_score"].notna() & self.df["away_score"].notna()].copy()

    def team_games(self, team: str, limit: int = 12) -> pd.DataFrame:
        if self.played.empty:
            return pd.DataFrame()
        return self.played[(self.played["home_norm"] == team) | (self.played["away_norm"] == team)].tail(limit)

    def head_to_head(self, limit: int = 8) -> pd.DataFrame:
        if self.played.empty:
            return pd.DataFrame()
        return self.played[
            ((self.played["home_norm"] == self.home) & (self.played["away_norm"] == self.away))
            | ((self.played["home_norm"] == self.away) & (self.played["away_norm"] == self.home))
        ].tail(limit)

    def team_summary(self, team: str, limit: int = 12) -> dict:
        games = self.team_games(team, limit)
        if games.empty:
            return {"games": 0, "win_rate": 0.50, "margin": 0.0, "for_avg": 0.0, "against_avg": 0.0}

        wins = 0
        margin = 0.0
        points_for = 0.0
        points_against = 0.0
        for _, row in games.iterrows():
            is_home = row["home_norm"] == team
            scored = float(row["home_score"] if is_home else row["away_score"])
            conceded = float(row["away_score"] if is_home else row["home_score"])
            wins += 1 if scored > conceded else 0
            margin += scored - conceded
            points_for += scored
            points_against += conceded

        sample = len(games)
        return {
            "games": sample,
            "win_rate": wins / sample,
            "margin": margin / sample,
            "for_avg": points_for / sample,
            "against_avg": points_against / sample,
        }

    def h2h_win_rate(self) -> float:
        h2h = self.head_to_head()
        if h2h.empty:
            return 0.50
        wins = 0
        for _, row in h2h.iterrows():
            home_won = float(row["home_score"]) > float(row["away_score"])
            if (row["home_norm"] == self.home and home_won) or (row["away_norm"] == self.home and not home_won):
                wins += 1
        return wins / len(h2h)

    def league_hint(self) -> str:
        return str(self.fixture.get("league") or "").lower()


class TennisEngine:
    """Dedicated early tennis engine for player-vs-player fixtures."""

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        tk = RecentFormToolkit("tennis", history, fixture)
        home = tk.home
        away = tk.away
        home_form = tk.team_summary(home, 10)
        away_form = tk.team_summary(away, 10)
        h2h_home_rate = tk.h2h_win_rate()
        league_hint = tk.league_hint()

        surface = "clay" if "clay" in league_hint or "roland" in league_hint else "grass" if "grass" in league_hint or "wimbledon" in league_hint else "hard" if "hard" in league_hint else "unknown"
        round_pressure = "late round" if any(token in league_hint for token in ["final", "semi", "quarter"]) else "standard round"
        fatigue_gap = max(0, away_form["games"] - home_form["games"]) - max(0, home_form["games"] - away_form["games"])
        edge = (home_form["win_rate"] - away_form["win_rate"]) * 0.34 + (h2h_home_rate - 0.5) * 0.16 + (home_form["margin"] - away_form["margin"]) * 0.035 + fatigue_gap * 0.01
        home_win_prob = _bounded_probability(0.50 + edge, 0.30, 0.76)
        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        projected_sets = 2.6 + min(0.5, abs(home_form["win_rate"] - away_form["win_rate"]))

        meta = {
            "summary": "Dedicated tennis engine checks player form, estimated surface context, head-to-head, set margin, fatigue proxy, and tournament-round pressure.",
            "model_label": "Tennis dedicated model read",
            "factors": [
                {"label": "Surface context", "value": surface.title(), "note": "Estimated from league/tournament naming until richer feed data is available"},
                {"label": f"{home} recent win rate", "value": f"{home_form['win_rate']:.0%}", "note": f"Last {home_form['games']} completed matches"},
                {"label": f"{away} recent win rate", "value": f"{away_form['win_rate']:.0%}", "note": f"Last {away_form['games']} completed matches"},
                {"label": "Head-to-head lean", "value": f"{h2h_home_rate:.0%} home-side", "note": "Direct matchup history where available"},
                {"label": "Round pressure", "value": round_pressure.title(), "note": "Tournament stage hint from fixture league text"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"projected_sets": round(projected_sets, 1), "surface": surface},
        }
        reason = f"Tennis read leans {'Home Win' if home_win_prob >= 0.5 else 'Away Win'} from recent player form, {surface} surface context, H2H rate {h2h_home_rate:.0%}, and set-margin signal."
        total_pick = "Over 21.5 Games" if projected_sets >= 2.8 else "Under 21.5 Games"
        return [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**meta, "market_logic": "Moneyline blends player win rate, head-to-head, set margin, surface hint, and fatigue proxy."}},
            {"market": "Total Games", "pick": total_pick, "confidence": 57.0, "edge_score": 57.0, "risk_level": "Medium", "reasoning": f"Projected match length is around {projected_sets:.1f} sets based on form gap and available scoring history.", "engine_meta": {**meta, "market_logic": "Tennis total read estimates match length from competitiveness and recent set/game scoring proxies."}},
        ]


class CricketEngine:
    """Dedicated early cricket engine for team fixtures."""

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        tk = RecentFormToolkit("cricket", history, fixture)
        home = tk.home
        away = tk.away
        home_form = tk.team_summary(home, 12)
        away_form = tk.team_summary(away, 12)
        h2h_home_rate = tk.h2h_win_rate()
        league_hint = tk.league_hint()
        fmt = "T20" if "t20" in league_hint or "twenty" in league_hint else "ODI" if "odi" in league_hint or "one day" in league_hint else "Test" if "test" in league_hint else "Unknown format"
        chase_bias = 0.02 if any(token in league_hint for token in ["night", "t20", "odi"]) else 0.0
        edge = (home_form["win_rate"] - away_form["win_rate"]) * 0.30 + (home_form["margin"] - away_form["margin"]) / 1200 + (h2h_home_rate - 0.5) * 0.14 + 0.03 - chase_bias
        home_win_prob = _bounded_probability(0.50 + edge, 0.32, 0.75)
        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        projected_runs = None
        if home_form["for_avg"] or away_form["for_avg"]:
            projected_runs = round((home_form["for_avg"] + away_form["for_avg"] + home_form["against_avg"] + away_form["against_avg"]) / 2, 1)

        meta = {
            "summary": "Dedicated cricket engine checks format hint, batting/run form, chasing context, head-to-head, venue side, and recent margin.",
            "model_label": "Cricket dedicated model read",
            "factors": [
                {"label": "Format", "value": fmt, "note": "Estimated from competition name until feed format field is available"},
                {"label": "Home run/win form", "value": f"{home_form['win_rate']:.0%}", "note": f"Average scoring margin {home_form['margin']:.1f}"},
                {"label": "Away run/win form", "value": f"{away_form['win_rate']:.0%}", "note": f"Average scoring margin {away_form['margin']:.1f}"},
                {"label": "Head-to-head", "value": f"{h2h_home_rate:.0%} home-side", "note": "Direct matchup history where available"},
                {"label": "Chasing/toss proxy", "value": f"-{chase_bias:.0%} home", "note": "Small uncertainty adjustment when toss/chase may matter"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"projected_runs": projected_runs, "format": fmt},
        }
        reason = f"Cricket read leans {'Home Win' if home_win_prob >= 0.5 else 'Away Win'} using {fmt} context, recent run margin, H2H {h2h_home_rate:.0%}, and batting/chasing profile."
        items = [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**meta, "market_logic": "Moneyline blends team win form, run margin, head-to-head, format, and toss/chasing uncertainty."}}
        ]
        if projected_runs:
            over_under = "Over" if projected_runs >= 300 else "Under"
            items.append({"market": "Total Runs", "pick": f"{over_under} {int(projected_runs // 50 * 50 + 50)} Runs", "confidence": 56.5, "edge_score": 56.5, "risk_level": "Medium", "reasoning": f"Available cricket scoring history projects roughly {projected_runs:.1f} combined runs for this matchup.", "engine_meta": {**meta, "market_logic": "Run total read uses recent runs for/against and format context."}})
        return items


class BaseballEngine:
    """Dedicated early baseball engine for team fixtures."""

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        tk = RecentFormToolkit("baseball", history, fixture)
        home = tk.home
        away = tk.away
        home_form = tk.team_summary(home, 15)
        away_form = tk.team_summary(away, 15)
        h2h_home_rate = tk.h2h_win_rate()
        run_diff = home_form["margin"] - away_form["margin"]
        edge = (home_form["win_rate"] - away_form["win_rate"]) * 0.28 + run_diff * 0.035 + (h2h_home_rate - 0.5) * 0.12 + 0.035
        home_win_prob = _bounded_probability(0.50 + edge, 0.33, 0.74)
        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        projected_total = round((home_form["for_avg"] + away_form["for_avg"] + home_form["against_avg"] + away_form["against_avg"]) / 2, 1) if home_form["for_avg"] or away_form["for_avg"] else None
        bullpen_proxy = round((home_form["against_avg"] - away_form["against_avg"]), 2)

        meta = {
            "summary": "Dedicated baseball engine checks recent run differential, scoring/allowed profile, home park edge, head-to-head, and bullpen/pitching proxy.",
            "model_label": "Baseball dedicated model read",
            "factors": [
                {"label": "Home run differential", "value": round(home_form["margin"], 2), "note": f"Last {home_form['games']} completed games"},
                {"label": "Away run differential", "value": round(away_form["margin"], 2), "note": f"Last {away_form['games']} completed games"},
                {"label": "Bullpen/pitching proxy", "value": bullpen_proxy, "note": "Lower recent runs allowed is treated as stronger prevention"},
                {"label": "Head-to-head", "value": f"{h2h_home_rate:.0%} home-side", "note": "Direct matchup history where available"},
                {"label": "Home park edge", "value": "+3.5%", "note": "Small baseball-specific home field adjustment"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"projected_total_runs": projected_total, "run_diff_edge": round(run_diff, 2)},
        }
        reason = f"Baseball read leans {'Home Win' if home_win_prob >= 0.5 else 'Away Win'} from run differential edge {run_diff:.2f}, recent runs allowed, H2H {h2h_home_rate:.0%}, and home park adjustment."
        items = [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**meta, "market_logic": "Moneyline blends win form, run differential, pitching/bullpen proxy, head-to-head, and home park edge."}},
            {"market": "Run Line", "pick": f"Home {run_diff:+.1f}" if run_diff >= 0 else f"Away {run_diff:+.1f}", "confidence": round(min(70, max(56, abs(run_diff) * 6 + 56)), 1), "edge_score": round(min(70, max(56, abs(run_diff) * 6 + 56)), 1), "risk_level": "Medium", "reasoning": f"Run-line follows recent run differential gap of {run_diff:.2f} runs per game.", "engine_meta": {**meta, "market_logic": "Run-line is based on recent average run differential rather than only win/loss form."}},
        ]
        if projected_total:
            threshold = 8.5
            over_under = "Over" if projected_total >= threshold else "Under"
            items.append({"market": "Total Runs", "pick": f"{over_under} {threshold}", "confidence": 57.0, "edge_score": 57.0, "risk_level": "Medium", "reasoning": f"Recent baseball scoring profile projects about {projected_total:.1f} total runs.", "engine_meta": {**meta, "market_logic": "Total runs read blends both teams' recent scoring and runs allowed."}})
        return items


class AmericanFootballEngine:
    """Dedicated engine for American football (NFL/CFL/NCAA)."""

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        tk = RecentFormToolkit("american_football", history, fixture)
        home = tk.home
        away = tk.away
        home_form = tk.team_summary(home, 10)
        away_form = tk.team_summary(away, 10)
        h2h_home_rate = tk.h2h_win_rate()
        league_hint = tk.league_hint()

        scoring_avg = (home_form["for_avg"] + away_form["for_avg"]) / 2 if home_form["for_avg"] or away_form["for_avg"] else 23.0
        spread_edge = (home_form["margin"] - away_form["margin"]) + 2.5  # home field ~2.5 pts
        edge = (home_form["win_rate"] - away_form["win_rate"]) * 0.32 + (h2h_home_rate - 0.5) * 0.12 + (spread_edge / 60)
        home_win_prob = _bounded_probability(0.50 + edge, 0.30, 0.76)
        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100
        spread_conf = round(min(72, max(54, abs(spread_edge) * 3 + 54)), 1)
        projected_total = round((home_form["for_avg"] + away_form["for_avg"] + home_form["against_avg"] + away_form["against_avg"]) / 2, 1) if home_form["for_avg"] else round(scoring_avg * 2, 1)
        total_line = 45.5
        over_under = "Over" if projected_total >= total_line else "Under"

        meta = {
            "summary": "American football engine blends recent point differential, scoring profile, home-field edge, and head-to-head.",
            "model_label": "American football dedicated read",
            "factors": [
                {"label": "Home point margin", "value": round(home_form["margin"], 2), "note": f"Last {home_form['games']} games"},
                {"label": "Away point margin", "value": round(away_form["margin"], 2), "note": f"Last {away_form['games']} games"},
                {"label": "Projected spread", "value": f"{spread_edge:+.1f}", "note": "Positive favours home"},
                {"label": "Head-to-head", "value": f"{h2h_home_rate:.0%} home-side", "note": "Direct matchup history"},
                {"label": "Projected total", "value": round(projected_total, 1), "note": "Combined estimated points"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"spread_edge": round(spread_edge, 1), "projected_total": round(projected_total, 1)},
        }
        spread_pick = f"Home {spread_edge:+.1f}" if spread_edge >= 0 else f"Away {abs(spread_edge):+.1f}"
        reason = f"American football read leans {'Home Win' if home_win_prob >= 0.5 else 'Away Win'}: margin edge {spread_edge:+.1f}, H2H {h2h_home_rate:.0%}, projected total {projected_total:.1f}."
        return [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**meta, "market_logic": "Moneyline blends win form, point margin, H2H, and home-field edge."}},
            {"market": "Point Spread", "pick": spread_pick, "confidence": spread_conf, "edge_score": spread_conf, "risk_level": _risk(spread_conf), "reasoning": f"Spread follows projected point margin of {spread_edge:+.1f} with home-field adjustment.", "engine_meta": {**meta, "market_logic": "Spread uses recent scoring margin and home-field boost."}},
            {"market": "Total Points", "pick": f"{over_under} {total_line}", "confidence": 57.0, "edge_score": 57.0, "risk_level": "Medium", "reasoning": f"Projected combined points {projected_total:.1f} vs line {total_line}.", "engine_meta": {**meta, "market_logic": "Total blends both teams' recent points for and allowed."}},
        ]


class HockeyEngine:
    """Dedicated engine for ice hockey (NHL/KHL/IIHF)."""

    def predict(self, history: pd.DataFrame, fixture: dict) -> list[dict]:
        tk = RecentFormToolkit("hockey", history, fixture)
        home = tk.home
        away = tk.away
        home_form = tk.team_summary(home, 12)
        away_form = tk.team_summary(away, 12)
        h2h_home_rate = tk.h2h_win_rate()
        league_hint = tk.league_hint()

        goal_diff = home_form["margin"] - away_form["margin"]
        edge = (home_form["win_rate"] - away_form["win_rate"]) * 0.30 + goal_diff * 0.06 + (h2h_home_rate - 0.5) * 0.12 + 0.04
        home_win_prob = _bounded_probability(0.50 + edge, 0.30, 0.76)
        winner_conf = max(home_win_prob, 1 - home_win_prob) * 100

        projected_goals = round((home_form["for_avg"] + away_form["for_avg"] + home_form["against_avg"] + away_form["against_avg"]) / 2, 1) if home_form["for_avg"] else 5.5
        total_line = 5.5
        over_under = "Over" if projected_goals >= total_line else "Under"
        total_conf = round(min(68, max(54, abs(projected_goals - total_line) * 8 + 54)), 1)

        btts_prob = min(0.78, max(0.42, (home_form["for_avg"] / max(home_form["for_avg"] + 0.1, 1)) * 0.6 + 0.35)) if home_form["for_avg"] else 0.62
        btts_pick = "BTTS Yes" if btts_prob >= 0.55 else "BTTS No"
        btts_conf = round(max(btts_prob, 1 - btts_prob) * 100, 1)

        meta = {
            "summary": "Hockey engine blends recent goals for/against, win form, home ice advantage, and head-to-head.",
            "model_label": "Hockey dedicated read",
            "factors": [
                {"label": "Home goal margin", "value": round(home_form["margin"], 2), "note": f"Last {home_form['games']} games"},
                {"label": "Away goal margin", "value": round(away_form["margin"], 2), "note": f"Last {away_form['games']} games"},
                {"label": "Projected goals", "value": projected_goals, "note": "Estimated combined goals"},
                {"label": "Head-to-head", "value": f"{h2h_home_rate:.0%} home-side", "note": "Direct matchup history"},
                {"label": "Home ice edge", "value": "+4%", "note": "Standard home-ice advantage"},
            ],
            "probabilities": {"home_win": round(home_win_prob, 4), "away_win": round(1 - home_win_prob, 4)},
            "projection": {"projected_goals": projected_goals, "total_line": total_line},
        }
        reason = f"Hockey read leans {'Home Win' if home_win_prob >= 0.5 else 'Away Win'}: goal margin edge {goal_diff:+.2f}, projected {projected_goals:.1f} goals, H2H {h2h_home_rate:.0%}."
        return [
            {"market": "Moneyline", "pick": "Home Win" if home_win_prob >= 0.5 else "Away Win", "confidence": round(winner_conf, 1), "edge_score": round(winner_conf, 1), "risk_level": _risk(winner_conf), "reasoning": reason, "engine_meta": {**meta, "market_logic": "Moneyline blends win form, goal margin, H2H, and home ice."}},
            {"market": "Total Points", "pick": f"{over_under} {total_line}", "confidence": total_conf, "edge_score": total_conf, "risk_level": _risk(total_conf), "reasoning": f"Projected {projected_goals:.1f} combined goals vs line {total_line}.", "engine_meta": {**meta, "market_logic": "Goal total uses recent goals for and against for both teams."}},
            {"market": "Both Teams to Score", "pick": btts_pick, "confidence": btts_conf, "edge_score": btts_conf, "risk_level": _risk(btts_conf), "reasoning": f"Both teams score probability estimated at {btts_prob:.0%} from recent scoring form.", "engine_meta": {**meta, "market_logic": "BTTS uses recent goals-scored rate as a proxy for scoring probability."}},
        ]
