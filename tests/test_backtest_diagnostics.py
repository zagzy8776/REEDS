"""Tests for the A/B/C backtest comparison and duplicate diagnostics."""
import numpy as np
import pytest

from app.ml.backtest_standings import _count_unique_profiles, _compare_results, _odds_baseline


class TestBacktestDiagnostics:
    """Tests for backtest duplicate detection and comparison logic."""

    def test_count_unique_profiles_uniform_predictions(self):
        probas = np.array([[1/3, 1/3, 1/3]] * 100)
        result = _count_unique_profiles(probas, tol=0.02)
        assert result["total"] == 100
        assert result["unique"] == 1
        assert result["duplicate_count"] == 99
        assert result["duplicate_pct"] == 99.0

    def test_count_unique_profiles_all_unique(self):
        probas = np.array([
            [0.1, 0.2, 0.7],
            [0.3, 0.4, 0.3],
            [0.5, 0.1, 0.4],
            [0.2, 0.7, 0.1],
            [0.8, 0.1, 0.1],
        ])
        result = _count_unique_profiles(probas, tol=0.02)
        assert result["total"] == 5
        assert result["unique"] == 5
        assert result["duplicate_count"] == 0
        assert result["duplicate_pct"] == 0.0

    def test_count_unique_profiles_near_identical_grouped(self):
        probas = np.array([
            [0.10, 0.85, 0.05],
            [0.105, 0.845, 0.05],
            [0.11, 0.84, 0.05],
            [0.70, 0.15, 0.15],
        ])
        result = _count_unique_profiles(probas, tol=0.02)
        assert result["total"] == 4
        assert result["unique"] == 3
        assert result["duplicate_count"] == 1
        assert result["duplicate_pct"] == 25.0

    def test_compare_results_identifies_b_beats_A(self):
        results = {
            "A_current_no_standings": {
                "accuracy": 0.50, "brier_score": 0.25, "log_loss": 1.1,
                "identifiable_predictions": {"total": 100, "unique": 10, "duplicate_count": 90, "duplicate_pct": 90.0},
            },
            "B_new_with_standings": {
                "accuracy": 0.52, "brier_score": 0.23, "log_loss": 1.0,
                "identifiable_predictions": {"total": 100, "unique": 50, "duplicate_count": 50, "duplicate_pct": 50.0},
            },
            "C_odds_baseline": {
                "accuracy": 0.48, "brier_score": 0.27, "log_loss": 1.2,
                "identifiable_predictions": {"total": 100, "unique": 1, "duplicate_count": 99, "duplicate_pct": 99.0},
            },
        }
        summary = _compare_results(results)
        assert summary["B_beats_A"] is True
        assert summary["B_beats_C"] is True
        assert summary["A_beats_C"] is True
        assert summary["duplicate_reduction_B_vs_A"] == 40.0
        assert summary["A_duplicate_predictions_pct"] == 90.0
        assert summary["B_duplicate_predictions_pct"] == 50.0

    def test_compare_results_b_does_not_beat_A_on_all_metrics(self):
        results = {
            "A_current_no_standings": {
                "accuracy": 0.50, "brier_score": 0.20, "log_loss": 0.9,
                "identifiable_predictions": {"total": 100, "unique": 10, "duplicate_count": 90, "duplicate_pct": 90.0},
            },
            "B_new_with_standings": {
                "accuracy": 0.52, "brier_score": 0.22, "log_loss": 1.0,
                "identifiable_predictions": {"total": 100, "unique": 20, "duplicate_count": 80, "duplicate_pct": 80.0},
            },
            "C_odds_baseline": {
                "accuracy": 0.48, "brier_score": 0.25, "log_loss": 1.1,
                "identifiable_predictions": {"total": 100, "unique": 1, "duplicate_count": 99, "duplicate_pct": 99.0},
            },
        }
        summary = _compare_results(results)
        assert summary["B_beats_A"] is False
        assert summary["accuracy_improvement_B_over_A"] == pytest.approx(0.02)
        assert summary["brier_score_improvement_B_over_A"] == pytest.approx(-0.02)

    def test_odds_baseline_with_valid_odds(self):
        import pandas as pd
        row = pd.Series({"home_odds": 2.0, "draw_odds": 3.5, "away_odds": 3.0})
        probs = _odds_baseline(row)
        assert len(probs) == 3
        assert sum(probs) == pytest.approx(1.0)
        assert all(p > 0 for p in probs)

    def test_odds_baseline_with_missing_odds(self):
        import pandas as pd
        row = pd.Series({"home_odds": None, "draw_odds": None, "away_odds": None})
        probs = _odds_baseline(row)
        assert len(probs) == 3
        assert all(abs(p - 1/3) < 1e-6 for p in probs)

    def test_odds_baseline_overround_removed(self):
        import pandas as pd
        row = pd.Series({"home_odds": 1.5, "draw_odds": 4.0, "away_odds": 6.0})
        probs = _odds_baseline(row)
        assert abs(sum(probs) - 1.0) < 1e-6
