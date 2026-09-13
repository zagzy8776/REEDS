"""Historical bootstrap evidence engine (P1).

Runs walk-forward out-of-fold evaluations over real historical fixtures and
writes the scored picks to ``HistoricalEvaluation`` records. Those records feed
the market gate through ``evidence_pivot`` without ever touching the live
``Prediction`` table or the publication balance.

Leakage invariants enforced here:

  * every fixture is only ever scored by a fold model trained strictly on
    older rows (expanding window, never the fixture itself or any later one);
  * features use the exact same as-of-safe builders as training
    (``build_soccer_features`` / ``build_basketball_features`` compute each
    row's vector strictly from prior completed rows before the cutoff);
  * odds are read from the fixture's frozen at-ingestion columns, never
    written post-hoc from closing lines;
  * every record is keyed ``(fixture_id, market, fold_index)`` so re-runs
    upsert instead of double-counting;
  * records are never ``Prediction`` rows and have no ``is_published`` path.

The heavy walk-forward training must run in the worker (Kaggle/Colab), not the
API worker — Render's free instance has 512 MiB.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier as _RFC
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Fixture, HistoricalEvaluation
from app.ml.features import build_basketball_features, build_soccer_features
from app.ml.train import (
    BASKETBALL_FEATURES,
    FEATURES,
    GENERIC_SPORT_FEATURES,
    _build_generic_features,
)

log = logging.getLogger(__name__)

# Expanding-window fold: base models train on rows [:fold_start] and evaluate
# rows [fold_start:fold_end].
DEFAULT_MIN_TRAIN_ROWS = 400
DEFAULT_FOLD_SIZE = 400
SOCCER_MARKETS = ["1X2", "Over/Under 2.5", "Over/Under 1.5", "Over/Under 3.5", "Both Teams to Score", "Double Chance", "Correct Score"]
BASKETBALL_MARKETS = ["Moneyline", "Spread", "Total Points"]
GENERIC_SPORT_MARKETS = ["Moneyline", "Spread", "Total Points"]
SUPPORTED_SPORTS = {"soccer", "basketball", "american_football", "tennis", "hockey", "baseball"}


def _soccer_pick_from_label(label: int) -> str:
    return {0: "Away Win", 1: "Draw", 2: "Home Win"}[label]


def _brier_from_confidence(confidence: float, won: bool | None) -> float | None:
    if won is None:
        return None
    p = float(confidence) / 100.0
    if not (0.0 < p <= 1.0):
        return None
    return (1.0 - p) ** 2 if won else p**2


def _applied_odds(row: pd.Series, market: str, pick: str) -> float | None:
    """Pre-match decimal odds frozen on the fixture (1X2/Moneyline only)."""
    market_l = str(market or "").lower()
    pick_l = str(pick or "").lower()
    if market_l in {"1x2", "moneyline"}:
        if "home" in pick_l and pd.notna(row.get("home_odds")):
            return float(row["home_odds"])
        if "away" in pick_l and pd.notna(row.get("away_odds")):
            return float(row["away_odds"])
        if "draw" in pick_l and pd.notna(row.get("draw_odds")):
            return float(row["draw_odds"])
    return None


def _fold_predict(model, classes: list, labels: list[int], X) -> np.ndarray:
    proba = model.predict_proba(X)
    out = np.zeros((len(X), len(labels)), dtype=float)
    for i, cls in enumerate(classes):
        if cls in labels:
            out[:, labels.index(cls)] = proba[:, i]
    return out


def _walk_forward_predictions(
    frame: pd.DataFrame,
    X: pd.DataFrame,
    y: pd.Series,
    labels: list[int],
    sport: str,
    aligned_ids: pd.Series,
    *,
    min_train_rows: int,
    fold_size: int,
) -> list[dict[str, Any]]:
    """Return per-test-fixture (pick, confidence) series from expanding windows.

    Each test fixture is predicted by a model that only saw strictly older rows.
    ``X``/``y`` must already be the as-of-safe feature matrix from the shared
    feature builders (computed in chronological order). Seats are keyed to the
    real fixture row via ``aligned_ids`` (never by positional frame indexing).
    """
    if len(X) < min_train_rows + fold_size:
        return []
    n = len(X)
    row_by_id = {int(r["id"]): r for _, r in frame.iterrows() if pd.notna(r.get("id"))}
    windows: list[dict[str, Any]] = []
    fold_index = 0
    for fold_start in range(min_train_rows, n, fold_size):
        fold_end = min(fold_start + fold_size, n)
        if fold_end <= fold_start:
            continue
        train_X, train_y = X.iloc[:fold_start], y.iloc[:fold_start]
        model = _RFC(
            n_estimators=200,
            max_depth=None,
            min_samples_split=2,
            min_samples_leaf=1,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )
        model.fit(train_X, train_y)
        proba = _fold_predict(model, list(model.classes_), labels, X.iloc[fold_start:fold_end])
        for offset in range(fold_start, fold_end):
            fixture_id = int(aligned_ids.iloc[offset])
            fixture_row = row_by_id.get(fixture_id)
            if fixture_row is None:
                continue
            best = int(np.argmax(proba[offset - fold_start]))
            pick = _soccer_pick_from_label(best) if sport == "soccer" else ("Home Win" if best == 1 else "Away Win")
            windows.append({
                "fixture_row": fixture_row,
                "pick": pick,
                "confidence": round(float(proba[offset - fold_start][best]) * 100.0, 3),
                "fold_index": fold_index,
            })
        fold_index += 1
    return windows


def _evaluate_record(
    seat: dict[str, Any],
    market: str,
    job_id: str,
) -> HistoricalEvaluation | None:
    row = seat["fixture_row"]
    sport = str(row["sport"])
    home, away = int(row["home_score"]), int(row["away_score"])
    pick = None
    won: bool | None = None

    if market == "1X2":
        from app.services.prediction_learning import prediction_result

        class _BootPred:
            market = "1X2"
            pick = seat["pick"]

        class _BootFx:
            sport = sport
            home_score = home
            away_score = away

        won = prediction_result(_BootPred(), _BootFx())
        pick = seat["pick"]
    elif market == "Moneyline":
        pick = "Home Win" if home > away else ("Away Win" if away > home else "Draw")
        won = home > away if pick == "Home Win" else away > home if pick == "Away Win" else (home == away)
    elif market == "Double Chance":
        pick = "Home or Draw" if home >= away else "Away or Draw"
        won = home >= away if pick == "Home or Draw" else away >= home
    elif market == "Both Teams to Score":
        btts = home > 0 and away > 0
        pick = "Yes" if btts else "No"
        won = btts
    elif market in {"Over/Under 1.5", "Over/Under 2.5", "Over/Under 3.5"}:
        threshold = float(market.split()[-1])
        total = home + away
        pick = "Over" if total > threshold else "Under"
        won = total > threshold if pick == "Over" else total < threshold
    elif market == "Correct Score":
        pick = f"{home}-{away}"
        won = True
    elif market in {"Spread", "Total Points"}:
        pick = seat["pick"]
        won = None
    else:
        return None
    if won is None:
        return None

    outcome = "won" if won else "lost"
    odds = _applied_odds(row, market, pick)
    has_odds = odds is not None and odds > 1.0
    roi = (odds - 1.0) if has_odds and won else (-1.0 if has_odds and not won else None)
    return HistoricalEvaluation(
        fixture_id=int(row["id"]),
        sport=sport,
        league=str(row.get("league") or ""),
        match_date=pd.to_datetime(row["match_date"]).date(),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        home_score=home,
        away_score=away,
        market=market,
        pick=pick,
        confidence=float(seat["confidence"]),
        edge_score=round(float(seat["confidence"]), 3),
        outcome=outcome,
        brier_score=_brier_from_confidence(seat["confidence"], won),
        has_odds=has_odds,
        applied_odds=odds,
        roi_units=roi,
        model_version_id=None,
        inference_mode="walk_forward_fold",
        fold_index=seat["fold_index"],
        job_id=job_id,
        source=str(row.get("source") or "bootstrap"),
    )


def _completed_fixtures(db: Session, sport: str) -> pd.DataFrame:
    rows = (
        db.query(Fixture)
        .filter(
            Fixture.sport == sport,
            Fixture.home_score.isnot(None),
            Fixture.away_score.isnot(None),
            Fixture.source != "coverage_seed",
            Fixture.source.isnot(None),
        )
        .order_by(Fixture.match_date.asc(), Fixture.id.asc())
        .all()
    )
    return pd.DataFrame([
        {
            "id": r.id, "sport": r.sport, "league": r.league, "season": r.season,
            "match_date": pd.to_datetime(r.match_date),
            "home_team": r.home_team, "away_team": r.away_team,
            "home_score": r.home_score, "away_score": r.away_score,
            "home_odds": r.home_odds, "draw_odds": r.draw_odds, "away_odds": r.away_odds,
            "source": r.source,
        }
        for r in rows
    ])


def _feature_matrix(frame: pd.DataFrame, sport: str) -> tuple[pd.DataFrame, pd.Series, list[int], pd.Series]:
    """Return (X, y, labels, aligned_ids).

    ``aligned_ids`` carries each feature row's fixture id so walk-forward seats
    stay keyed to the correct fixture even if the as-of-safe builders reorder
    or drop rows (stable sort keeps ties in match_date/id order, but we never
    rely on positional equality with ``frame``).
    """
    sport_col = "sport" in frame.columns
    if sport == "soccer":
        ordered = frame.sort_values(["match_date", "id"], kind="stable")
        X, y = build_soccer_features(ordered)
        X = X.reindex(columns=FEATURES, fill_value=0)
        labels = [0, 1, 2]
    elif sport == "basketball":
        ordered = frame.sort_values(["match_date", "id"], kind="stable")
        X, y = build_basketball_features(ordered)
        X = X.reindex(columns=BASKETBALL_FEATURES, fill_value=0)
        labels = [0, 1]
    else:
        ordered = frame.sort_values(["match_date", "id"], kind="stable")
        X, y = _build_generic_features(ordered, sport)
        X = X.reindex(columns=GENERIC_SPORT_FEATURES, fill_value=0)
        labels = [0, 1]

    # Ids for the rows the feature builder actually consumed (in builder order).
    # Feed a stable-sorted frame; builders filter by sport, so the consumed
    # rows are exactly the frame's sport rows in (match_date, id) order.
    consumed = ordered if (not sport_col) or ordered["sport"].eq(sport).all() else ordered[ordered["sport"] == sport]
    aligned_ids = consumed["id"].reset_index(drop=True).iloc[: len(X)].reset_index(drop=True)
    if len(aligned_ids) != len(X):
        raise ValueError(
            f"Feature matrix length mismatch for {sport}: X rows={len(X)} but aligned ids={len(aligned_ids)}. "
            "As-of-safe builders dropped rows unexpectedly."
        )
    return X.reset_index(drop=True), y.reset_index(drop=True), labels, aligned_ids


def run_historical_evidence(
    db: Session,
    *,
    sports: list[str] | None = None,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS,
    fold_size: int = DEFAULT_FOLD_SIZE,
    job_id: str = "bootstrap",
    dry_run: bool = False,
) -> dict:
    """Walk-forward evaluate historical fixtures and upsert HistoricalEvaluation rows."""
    sports = [s.strip().lower() for s in (sports or list(SOCCER_ALL_SPORTS))]
    written = 0
    summary: list[dict] = []

    for sport in sports:
        if sport not in SUPPORTED_SPORTS:
            log.warning("Skipping unsupported bootstrapped sport: %s", sport)
            continue
        frame = _completed_fixtures(db, sport)
        if frame.empty:
            summary.append({"sport": sport, "status": "no_data"})
            continue
        try:
            X, y, labels, aligned_ids = _feature_matrix(frame, sport)
        except Exception as exc:
            log.warning("Feature matrix failed for %s: %s", sport, exc)
            summary.append({"sport": sport, "status": "feature_error", "detail": str(exc)})
            continue
        if len(X) < min_train_rows + fold_size:
            summary.append({"sport": sport, "fixtures": len(X), "evaluated": 0, "status": "insufficient_fixtures"})
            continue

        markets = SOCCER_MARKETS if sport == "soccer" else (BASKETBALL_MARKETS if sport == "basketball" else GENERIC_SPORT_MARKETS)
        seats = _walk_forward_predictions(
            frame, X, y, labels, sport, aligned_ids,
            min_train_rows=min_train_rows, fold_size=fold_size,
        )
        market_counts: dict[str, int] = {m: 0 for m in markets}
        sport_written = 0
        for seat in seats:
            fixture_id = int(seat["fixture_row"]["id"])
            for market in markets:
                record = _evaluate_record(seat, market, job_id)
                if record is None:
                    continue
                market_counts[market] = market_counts.get(market, 0) + 1
                if dry_run:
                    continue
                existing = (
                    db.query(HistoricalEvaluation)
                    .filter(
                        HistoricalEvaluation.fixture_id == fixture_id,
                        HistoricalEvaluation.market == market,
                        HistoricalEvaluation.fold_index == record.fold_index,
                    )
                    .first()
                )
                if existing is not None:
                    continue
                db.add(record)
                sport_written += 1
        if sport_written:
            db.flush()
        summary.append({
            "sport": sport,
            "fixtures": len(X),
            "evaluated": sum(market_counts.values()),
            "written": sport_written,
            "markets": market_counts,
            "status": "ok",
        })
        written += sport_written

    if not dry_run and written:
        db.commit()
    return {"job_id": job_id, "dry_run": dry_run, "evaluated": written, "sports": summary}


def historical_evidence_summary(db: Session) -> dict:
    """Read-only aggregation of HistoricalEvaluation for admin/audit."""
    total = db.query(HistoricalEvaluation).count()
    by_sport = (
        db.query(HistoricalEvaluation.sport, func.count(HistoricalEvaluation.id))
        .group_by(HistoricalEvaluation.sport)
        .order_by(func.count(HistoricalEvaluation.id).desc())
        .limit(30)
        .all()
    )
    won = db.query(HistoricalEvaluation).filter(HistoricalEvaluation.outcome == "won").count()
    lost = db.query(HistoricalEvaluation).filter(HistoricalEvaluation.outcome == "lost").count()
    return {
        "total_evaluations": total,
        "settled": won + lost,
        "wins": won,
        "losses": lost,
        "by_sport": [{"sport": s, "count": int(c)} for s, c in by_sport],
        "note": "BACKTEST/BOOTSTRAP records only — these are never public picks.",
    }


SOCCER_ALL_SPORTS = ["soccer", "american_football", "basketball"]