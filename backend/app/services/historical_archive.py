"""CockroachDB historical archive helpers for REEDS inference."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def _connect():
    import psycopg
    from app.core.config import get_settings

    url = (get_settings().cockroach_database_url or os.environ.get("COCKROACH_DATABASE_URL") or "").strip()
    if not url:
        return None
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    # Cockroach's documented CA location is ~/.postgresql/root.crt. Do not put
    # certificates or credentials in the repository.
    cert = Path.home() / ".postgresql" / "root.crt"
    kwargs = {}
    if cert.exists():
        kwargs["sslrootcert"] = str(cert)
    return psycopg.connect(url, **kwargs)


def load_archive_dataframe(max_age_days: int | None = None) -> pd.DataFrame:
    """Load completed historical fixtures from Cockroach into a pandas frame."""
    conn = None
    try:
        conn = _connect()
        if conn is None:
            return pd.DataFrame()
        sql = """
            SELECT id, sport, league, season, match_date, home_team, away_team,
                   home_score, away_score, home_odds, draw_odds, away_odds
            FROM fixtures_archive
            WHERE match_date IS NOT NULL
              AND home_team IS NOT NULL
              AND away_team IS NOT NULL
        """
        params = None
        if max_age_days is not None:
            sql += " AND match_date >= current_date - (%s * INTERVAL '1 day')"
            params = (int(max_age_days),)
        sql += " ORDER BY match_date ASC, id ASC"
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        log.exception("Could not load Cockroach historical archive")
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()
