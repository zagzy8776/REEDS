"""Standings-derived features for soccer prediction.

This module computes league-table features for a fixture from the DB's
``standings`` table. All lookups use ``effective_date < match_date`` to
prevent lookahead leakage (a team's final table position can never be
used to predict an earlier match).

Feature list:
  - league_position / table_position_diff
  - points_per_game, win_rate, goal_difference_per_game
  - goals_for_per_game, goals_against_per_game
  - home_points_per_game, away_points_per_game
  - form_score (recent_form → W=1, D=0.5, L=0)
  - strength_difference (goal diff per game delta)
  - has_standings_data (0/1 flag for model awareness)

Enabled via the ENABLE_STANDINGS_FEATURES environment flag (default OFF).
"""
from __future__ import annotations

import os
from datetime import date, datetime

ENABLE_STANDINGS_FEATURES = os.environ.get("ENABLE_STANDINGS_FEATURES", "").lower() in (
    "1", "true", "yes", "on",
)

DEFAULTS = {
    "home_league_position": 10,
    "away_league_position": 10,
    "home_table_points": 45,
    "away_table_points": 45,
    "home_table_points_per_game": 1.4,
    "away_table_points_per_game": 1.4,
    "home_table_goals_for": 30,
    "home_table_goals_against": 30,
    "away_table_goals_for": 30,
    "away_table_goals_against": 30,
    "home_table_goal_difference": 0,
    "away_table_goal_difference": 0,
    "home_win_rate": 0.33,
    "away_win_rate": 0.33,
    "home_goal_diff_per_game": 0.0,
    "away_goal_diff_per_game": 0.0,
    "home_goals_for_per_game": 1.3,
    "away_goals_for_per_game": 1.1,
    "home_goals_against_per_game": 1.2,
    "away_goals_against_per_game": 1.3,
    "home_form_score": 1.2,
    "away_form_score": 1.2,
    "table_position_diff": 0,
    "table_points_diff": 0,
    "strength_difference": 0,
    "has_standings_data": 0,
}

FORM_WEIGHTS = {"W": 1.0, "D": 0.5, "L": 0.0}


def resolve_standings_features(
    db,
    league: str,
    home_team: str,
    away_team: str,
    fixture_date: date | str | None,
) -> dict:
    """Return standings-derived features for a fixture.

    Returns DEFAULTS (no leakage, no fabrication) when:
      - ENABLE_STANDINGS_FEATURES is False
      - db is None or league is None or fixture_date is None
      - no standings rows are found for either team

    When standings ARE found, enriches the defaults with real league-table
    data. effective_date lookups strictly use ``< fixture_date``.
    """
    if not ENABLE_STANDINGS_FEATURES:
        return dict(DEFAULTS)

    if db is None or league is None or fixture_date is None:
        return dict(DEFAULTS)

    if isinstance(fixture_date, str):
        fixture_date = datetime.strptime(fixture_date, "%Y-%m-%d").date()

    try:
        from sqlalchemy import select
        from app.db.models import Standing
        from app.utils.team_names import normalize_team_name

        home = normalize_team_name(home_team, "soccer")
        away = normalize_team_name(away_team, "soccer")

        def _lookup(team: str):
            return db.execute(
                select(Standing).where(
                    Standing.sport == "soccer",
                    Standing.league == league,
                    Standing.team == team,
                    Standing.standing_type == "total",
                    Standing.effective_date < fixture_date,
                ).order_by(Standing.effective_date.desc()).limit(1)
            ).scalar_one_or_none()

        home_st = _lookup(home)
        away_st = _lookup(away)

        features = dict(DEFAULTS)

        for team_st, prefix in [(home_st, "home"), (away_st, "away")]:
            if team_st is None:
                continue

            gp = team_st.games_played or 1
            pts = team_st.points or 0
            gf = team_st.goals_for or 0
            ga = team_st.goals_against or 0
            gd = team_st.goal_difference or (gf - ga)

            features[f"{prefix}_league_position"] = team_st.position or 10
            features[f"{prefix}_table_points"] = pts
            features[f"{prefix}_table_points_per_game"] = pts / max(gp, 1)
            features[f"{prefix}_table_goals_for"] = gf
            features[f"{prefix}_table_goals_against"] = ga
            features[f"{prefix}_table_goal_difference"] = gd

            if team_st.wins is not None:
                features[f"{prefix}_win_rate"] = team_st.wins / max(gp, 1)

            features[f"{prefix}_goal_diff_per_game"] = gd / max(gp, 1)
            features[f"{prefix}_goals_for_per_game"] = gf / max(gp, 1)
            features[f"{prefix}_goals_against_per_game"] = ga / max(gp, 1)

            # Form score from recent_form string (W/D/L)
            form = team_st.recent_form
            if form:
                valid = [c for c in str(form).upper() if c in FORM_WEIGHTS]
                if valid:
                    features[f"{prefix}_form_score"] = sum(FORM_WEIGHTS[c] for c in valid) / len(valid)
                else:
                    features[f"{prefix}_form_score"] = 1.2
            else:
                features[f"{prefix}_form_score"] = 1.2

            features["has_standings_data"] = 1

        # Cross-team derived features
        if home_st is not None and away_st is not None:
            home_gp = home_st.games_played or 1
            away_gp = away_st.games_played or 1
            features["table_position_diff"] = (
                (home_st.position or 10) - (away_st.position or 10)
            )
            features["table_points_diff"] = (
                (home_st.points or 45) - (away_st.points or 45)
            )
            home_gdpg = ((home_st.goal_difference or 0) / max(home_gp, 1))
            away_gdpg = ((away_st.goal_difference or 0) / max(away_gp, 1))
            features["strength_difference"] = home_gdpg - away_gdpg

        return features

    except Exception:
        return dict(DEFAULTS)


def standings_feature_columns() -> list[str]:
    """Return all feature names produced by resolve_standings_features."""
    return list(DEFAULTS.keys())
