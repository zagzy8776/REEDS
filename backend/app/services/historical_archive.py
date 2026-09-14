"""CockroachDB historical archive helpers for REEDS inference."""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

from app.db import roles as db_roles
from app.db.session import get_role_engine

log = logging.getLogger(__name__)

_ARCHIVE_SQL = text(
    """
    SELECT id, sport, league, season, match_date, home_team, away_team,
           home_score, away_score, home_odds, draw_odds, away_odds
    FROM fixtures_archive
    WHERE match_date IS NOT NULL
      AND home_team IS NOT NULL
      AND away_team IS NOT NULL
    ORDER BY match_date ASC, id ASC
    """
)


def load_archive_dataframe(max_age_days: int | None = None) -> pd.DataFrame:
    """Load completed historical fixtures from Cockroach into a pandas frame."""
    engine = get_role_engine(db_roles.COCKROACH)
    if engine is None:
        return pd.DataFrame()
    try:
        sql = _ARCHIVE_SQL
        params = {}
        if max_age_days is not None:
            sql = text(
                """
                SELECT id, sport, league, season, match_date, home_team, away_team,
                       home_score, away_score, home_odds, draw_odds, away_odds
                FROM fixtures_archive
                WHERE match_date >= current_date - (:days * INTERVAL '1 day')
                  AND match_date IS NOT NULL
                  AND home_team IS NOT NULL
                  AND away_team IS NOT NULL
                ORDER BY match_date ASC, id ASC
                """
            )
            params = {"days": int(max_age_days)}
        with engine.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        log.exception("Could not load Cockroach historical archive")
        return pd.DataFrame()
