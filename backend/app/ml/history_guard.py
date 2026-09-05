"""Centralized temporal/data-quality guard for prediction inputs."""
from __future__ import annotations

import pandas as pd


def sanitize_prediction_history(history: pd.DataFrame, fixture: dict) -> pd.DataFrame:
    """Return only completed historical rows strictly before the target fixture.

    Prediction engines frequently receive broad bulk-ingested history. Keeping the
    cutoff here ensures every downstream sport engine sees the same point-in-time
    dataset and cannot accidentally learn from future fixtures.
    """
    if history is None or history.empty:
        return pd.DataFrame() if history is None else history.copy()

    df = history.copy()

    if "sport" in df.columns and fixture.get("sport"):
        df = df[df["sport"] == fixture.get("sport")].copy()

    if "home_score" in df.columns and "away_score" in df.columns:
        df = df[df["home_score"].notna() & df["away_score"].notna()].copy()

    if "match_date" in df.columns:
        target = pd.to_datetime(fixture.get("match_date"), errors="coerce")
        dates = pd.to_datetime(df["match_date"], errors="coerce")
        if not pd.isna(target):
            df = df[dates < target].copy()
        df["match_date"] = dates.loc[df.index]
        df = df.sort_values("match_date", kind="mergesort", na_position="last")

    return df.reset_index(drop=True)
