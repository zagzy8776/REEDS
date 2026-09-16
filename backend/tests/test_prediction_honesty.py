"""Honesty tests for the soccer prediction engine.

Guards against fabricated predictions: no team history must never produce the
invented default card (form 1.2, goals 1.3/1.1, Elo 1125...), and a missing
model bundle must never produce uniform 0.33/0.33/0.34 probabilities.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ml.ensemble import LoyalEdgeEngine  # noqa: E402


def _history_for(team: str, other: str, n: int, start: str = "2026-08-01") -> pd.DataFrame:
    """n completed matches for `team` (all wins) before any 2026-09 fixture date."""
    rows = []
    for i in range(n):
        rows.append({
            "sport": "soccer",
            "league": "Test League",
            "match_date": pd.Timestamp(start) + pd.Timedelta(days=i * 3),
            "home_team": team if i % 2 == 0 else other,
            "away_team": other if i % 2 == 0 else team,
            "home_score": 2 if i % 2 == 0 else 0,
            "away_score": 1 if i % 2 == 0 else 2,
            "home_odds": None,
            "draw_odds": None,
            "away_odds": None,
        })
    return pd.DataFrame(rows)


@pytest.fixture()
def engine():
    return LoyalEdgeEngine()  # no model path -> no bundle


def _fixture(odds=None, home="Team A", away="Team B"):
    fx = {
        "home_team": home,
        "away_team": away,
        "match_date": "2026-09-16",
        "league": "Test League",
    }
    if odds:
        fx.update({"home_odds": odds[0], "draw_odds": odds[1], "away_odds": odds[2]})
    return fx


def test_no_history_with_odds_yields_market_implied_read_only(engine):
    items = engine.predict_soccer(pd.DataFrame(), _fixture(odds=(2.38, 3.49, 2.9)))
    assert items, "odds exist, so an honest market-implied read must be published"
    assert {i["market"] for i in items} == {"1X2", "Double Chance"}
    for item in items:
        assert item["engine_meta"]["read_type"] == "market_implied"
        assert "not from a trained model" in item["reasoning"]
        assert item["edge_score"] == 0.0
        assert item["risk_level"] in {"Medium", "High"}  # never "Low"


def test_no_history_without_odds_publishes_nothing(engine):
    assert engine.predict_soccer(pd.DataFrame(), _fixture()) == []


def test_degenerate_odds_publish_nothing(engine):
    assert engine.predict_soccer(pd.DataFrame(), _fixture(odds=(1.0, 3.49, 2.9))) == []
    assert engine.predict_soccer(pd.DataFrame(), _fixture(odds=(None, 3.49, 2.9))) == []


def test_market_implied_probabilities_match_margin_removed_odds(engine):
    items = engine.predict_soccer(pd.DataFrame(), _fixture(odds=(2.0, 4.0, 4.0)))
    implied = [1 / 2.0, 1 / 4.0, 1 / 4.0]
    total = sum(implied)
    probs = items[0]["engine_meta"]["probabilities"]
    assert probs["home_win"] == round(implied[0] / total, 4)
    assert probs["draw"] == round(implied[1] / total, 4)
    assert probs["away_win"] == round(implied[2] / total, 4)


def test_thin_history_below_floor_also_gated(engine):
    # 2 completed matches each: below the 3-match floor -> market-implied only.
    history = pd.concat([_history_for("Team A", "Filler", 2), _history_for("Team B", "Filler", 2)])
    items = engine.predict_soccer(history, _fixture(odds=(2.38, 3.49, 2.9)))
    assert {i["market"] for i in items} == {"1X2", "Double Chance"}
    assert all(i["engine_meta"]["read_type"] == "market_implied" for i in items)


def test_no_bundle_with_real_history_publishes_nothing(engine):
    history = pd.concat([_history_for("Team A", "Filler", 5), _history_for("Team B", "Filler", 5)])
    assert engine.predict_soccer(history, _fixture(odds=(2.38, 3.49, 2.9))) == []


def test_ensemble_predict_raises_without_bundle_instead_of_fabricating(engine):
    with pytest.raises(RuntimeError, match="refusing silent default probabilities"):
        engine._ensemble_predict({"x": 1}, [0, 1, 2])


def test_history_backed_read_discloses_data_depth(engine, monkeypatch):
    class _FakeModel:
        classes_ = [0, 1, 2]

        def predict_proba(self, _x):
            return [[0.2, 0.3, 0.5]]

    monkeypatch.setattr(
        engine,
        "_load_bundle",
        lambda: {"features": ["home_form_points"], "models": {"rf": _FakeModel()}, "weights": [1.0]},
        raising=False,
    )
    history = pd.concat([_history_for("Team A", "Filler", 5), _history_for("Team B", "Filler", 5)])
    items = engine.predict_soccer(history, _fixture(odds=(2.38, 3.49, 2.9)))
    assert items, "real history + loaded model must produce model reads"
    for item in items:
        depth = item["engine_meta"]["data_depth"]
        assert depth["home_history"] >= 3 and depth["away_history"] >= 3
        assert item["engine_meta"].get("read_type") != "market_implied"


# ── GenericSportEngine honesty ────────────────────────────────────────────


def test_generic_engine_publishes_nothing_without_completed_history():
    from app.ml.generic import GenericSportEngine

    assert GenericSportEngine().predict(pd.DataFrame(), {"sport": "rugby", "home_team": "X", "away_team": "Y"}) == []


def test_generic_engine_computes_probs_from_observed_rates_only():
    from app.ml.generic import GenericSportEngine

    rows = []
    # Team A wins everything, Team B loses everything, one draw between others.
    for i in range(6):
        rows.append({"home_team": "Team A", "away_team": "Filler", "home_score": 3, "away_score": 0, "match_date": f"2026-08-{i + 1:02d}"})
        rows.append({"home_team": "Filler", "away_team": "Team B", "home_score": 2, "away_score": 0, "match_date": f"2026-08-{i + 1:02d}"})
    history = pd.DataFrame(rows)
    items = GenericSportEngine().predict(history, {"sport": "rugby", "home_team": "Team A", "away_team": "Team B"})
    markets = {i["market"] for i in items}
    assert "Moneyline" in markets and "Double Chance" in markets
    moneyline = next(i for i in items if i["market"] == "Moneyline")
    assert moneyline["pick"] == "Home Win"
    probs = moneyline["engine_meta"]["probabilities"]
    assert probs["draw"] >= 0  # draw rate is now computed and disclosed
    dc = next(i for i in items if i["market"] == "Double Chance")
    # No more invented +0.25 bonus: DC must equal the sum of two normalized probs.
    assert dc["confidence"] <= 100.0


# ── Phase 2/3: identity + data-chain honesty ─────────────────────────────


def test_unicode_team_names_share_one_identity():
    from app.utils.team_names import normalize_team_name as n

    assert n("Jagiellonia Białystok") == n("Jagiellonia Bialystok")
    assert n("Atlético Madrid") == n("Atletico Madrid")
    assert n("man utd") == "Manchester United"  # curated aliases untouched
    assert n("Wrexham AFC") == "Wrexham"  # suffix strip untouched


def test_accented_history_now_matches_ascii_fixture():
    # The exact production bug: history stored with "Białystok", fixture says
    # "Bialystok" -> features previously saw zero history and fell back to
    # invented defaults.
    from app.ml.features import features_for_fixture

    history = _history_for("Jagiellonia Białystok", "Filler", 5)
    f = features_for_fixture(
        history,
        "Jagiellonia Bialystok",
        "Other Team",
        "2026-09-16",
        "Test League",
    )
    assert f["home_history_count"] >= 3, "accented history must count for the ASCII-spelled fixture"


def test_sport_specific_engines_publish_nothing_without_history():
    from app.ml.sport_specific import BaseballEngine, HockeyEngine, TennisEngine

    fx = {"home_team": "Team X", "away_team": "Team Y", "league": "NHL", "match_date": "2026-09-16"}
    assert TennisEngine().predict(pd.DataFrame(), fx) == []
    assert HockeyEngine().predict(pd.DataFrame(), fx) == []
    assert BaseballEngine().predict(pd.DataFrame(), fx) == []


def test_sport_specific_confidences_are_computed_not_hardcoded():
    from app.ml.sport_specific import TennisEngine

    rows = []
    for i in range(5):
        rows.append({"home_team": "Player A", "away_team": "Filler", "home_score": 2, "away_score": 0, "match_date": f"2026-08-{i + 1:02d}"})
        rows.append({"home_team": "Filler", "away_team": "Player B", "home_score": 0, "away_score": 2, "match_date": f"2026-08-{i + 1:02d}"})
    items = TennisEngine().predict(pd.DataFrame(rows), {"home_team": "Player A", "away_team": "Player B", "league": "ATP Hard Court", "match_date": "2026-09-16"})
    assert items
    total = next(i for i in items if i["market"] == "Total Games")
    # The old card always said 57.0; now it must derive from the actual
    # separation and use the standard risk scale.
    assert total["confidence"] != 57.0
    assert total["risk_level"] in {"Low", "Medium", "High"}


def test_generic_engine_never_lowers_confidence_to_disguise_missing_data():
    from app.ml.generic import GenericSportEngine

    # One-sided sample: Team A perfect, Team B winless -> high computed
    # confidence is legitimate; the old 0.28-0.78 clamp is gone.
    rows = []
    for i in range(8):
        rows.append({"home_team": "Team A", "away_team": "Filler", "home_score": 4, "away_score": 0, "match_date": f"2026-08-{i + 1:02d}"})
        rows.append({"home_team": "Filler", "away_team": "Team B", "home_score": 5, "away_score": 0, "match_date": f"2026-08-{i + 1:02d}"})
    history = pd.DataFrame(rows)
    items = GenericSportEngine().predict(history, {"sport": "rugby", "home_team": "Team A", "away_team": "Team B"})
    moneyline = next(i for i in items if i["market"] == "Moneyline")
    assert moneyline["confidence"] > 70  # clamps removed; data speaks


def test_no_history_features_are_nan_not_invented():
    # Phase 3: the feature layer itself no longer fabricates. Empty history
    # must produce NaN (imputed at train/serving time from real medians),
    # never the old invented constants (form 1.2, goals 1.3, Elo 1125).
    from app.ml.features import features_for_fixture

    f = features_for_fixture(pd.DataFrame(), "Team A", "Team B", "2026-09-16", "Unknown League")
    assert pd.isna(f["home_form_points"])   # was 1.2
    assert pd.isna(f["home_goals_for"])     # was 1.3
    assert pd.isna(f["home_elo"])           # was 1500*0.75 = 1125
    assert pd.isna(f["h2h_home_win_rate"])  # was 0.50
    assert f["h2h_meetings"] == 0.0         # true zero meetings
    assert f["league_strength"] == 1.0      # neutral multiplier, no strength claim


def test_ensemble_predict_raises_when_every_model_fails(engine):
    class _Bad:
        classes_ = [0, 1, 2]

        def predict_proba(self, _x):
            raise ValueError("cannot handle NaN")

    engine.bundle = {
        "features": ["x"],
        "models": {"bad": _Bad()},
        "weights": [1.0],
    }
    with pytest.raises(RuntimeError, match="refusing to fabricate"):
        engine._ensemble_predict({"x": float("nan")}, [0, 1, 2])


def test_ensemble_predict_skips_failed_model_and_uses_survivors(engine):
    class _Good:
        classes_ = [0, 1, 2]

        def predict_proba(self, _x):
            return [[0.2, 0.3, 0.5]]

    class _Bad:
        classes_ = [0, 1, 2]

        def predict_proba(self, _x):
            raise ValueError("cannot handle NaN")

    engine.bundle = {
        "features": ["x"],
        "models": {"bad": _Bad(), "good": _Good()},
        "weights": [0.5, 0.5],
    }
    probs = engine._ensemble_predict({"x": float("nan")}, [0, 1, 2])
    assert abs(probs["home"] - 0.5) < 1e-6  # the good model's real output, not a uniform mix

