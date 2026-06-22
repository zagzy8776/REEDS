"""Test script for enhanced mathematical models."""

import os
import sys
from datetime import datetime, timedelta

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_value_betting_engine():
    """Test the value betting engine."""
    from app.ml.value_engine import ValueBettingEngine, PoissonValueEngine
    
    print("Testing Value Betting Engine...")
    
    # Test basic value calculations
    engine = ValueBettingEngine(bankroll=1000.0, kelly_fraction=0.25)
    
    # Test implied probability
    odds = 2.0
    implied = engine.implied_probability(odds)
    print(f"Implied probability for odds {odds}: {implied:.4f} (expected: 0.5000)")
    assert 0.49 <= implied <= 0.51, "Implied probability calculation incorrect"
    
    # Test edge calculation
    model_prob = 0.60
    edge = engine.calculate_edge(model_prob, odds)
    print(f"Edge for model prob {model_prob} and odds {odds}: {edge:.4f} (expected: ~0.10)")
    assert edge > 0.05, "Edge calculation incorrect"
    
    # Test expected value
    ev = engine.expected_value(model_prob, odds)
    print(f"Expected value: {ev:.4f} (expected: 0.20)")
    assert ev > 0.15, "Expected value calculation incorrect"
    
    # Test Kelly criterion
    kelly = engine.kelly_criterion(model_prob, odds)
    print(f"Kelly stake: {kelly:.4f} (expected: ~0.10)")
    assert 0.05 <= kelly <= 0.15, "Kelly criterion calculation incorrect"
    
    print("✓ Value Betting Engine tests passed!\n")
    
    # Test Poisson Value Engine
    print("Testing Poisson Value Engine...")
    poisson_engine = PoissonValueEngine()
    
    # Test Poisson probability
    lam = 2.0
    prob_2_goals = poisson_engine.poisson_probability(lam, 2)
    print(f"Poisson P(X=2) for λ={lam}: {prob_2_goals:.4f} (expected: ~0.2707)")
    assert 0.25 <= prob_2_goals <= 0.29, "Poisson probability calculation incorrect"
    
    # Test Over/Under probability
    home_exp = 1.5
    away_exp = 1.2
    over_2_5 = poisson_engine.calculate_over_under_prob(home_exp, away_exp, 2.5)
    print(f"P(Over 2.5) for expected goals {home_exp}+{away_exp}: {over_2_5:.4f}")
    assert 0.0 <= over_2_5 <= 1.0, "Over/Under probability out of range"
    
    # Test BTTS probability
    btts = poisson_engine.calculate_btts_prob(home_exp, away_exp)
    print(f"P(BTTS) for expected goals {home_exp}+{away_exp}: {btts:.4f}")
    assert 0.0 <= btts <= 1.0, "BTTS probability out of range"
    
    print("✓ Poisson Value Engine tests passed!\n")


def test_elo_engine():
    """Test the Elo rating system."""
    from app.ml.value_engine import EloValueEngine
    
    print("Testing Elo Engine...")
    elo_engine = EloValueEngine()
    
    # Set initial ratings
    elo_engine.set_elo_rating("Team A", 1600)
    elo_engine.set_elo_rating("Team B", 1500)
    
    # Test expected score
    expected_a = elo_engine.expected_score(1600, 1500)
    print(f"Expected score for Team A (1600 vs 1500): {expected_a:.4f} (expected: ~0.64)")
    assert 0.60 <= expected_a <= 0.68, "Expected score calculation incorrect"
    
    # Test rating update
    new_rating = elo_engine.update_elo("Team A", 1500, 1.0)  # Team A wins
    print(f"New rating for Team A after win: {new_rating:.2f} (expected: ~1611)")
    assert 1608 <= new_rating <= 1614, "Elo update calculation incorrect"
    
    # Test prediction from Elo
    probs = elo_engine.predict_from_elo("Team A", "Team B")
    print(f"Match probabilities: Home {probs['home_win']:.4f}, Draw {probs['draw']:.4f}, Away {probs['away_win']:.4f}")
    assert abs(sum(probs.values()) - 1.0) < 0.01, "Probabilities don't sum to 1"
    
    print("✓ Elo Engine tests passed!\n")


def test_advanced_features():
    """Test advanced feature engineering."""
    from app.ml.advanced_features import AdvancedFeatureEngineer
    import pandas as pd
    
    print("Testing Advanced Feature Engineering...")
    
    # Create sample historical data
    data = {
        'match_date': pd.date_range(start='2024-01-01', periods=20, freq='D'),
        'home_team': ['Team A', 'Team B', 'Team A', 'Team C', 'Team B', 'Team A', 'Team C', 'Team B', 'Team A', 'Team C'] * 2,
        'away_team': ['Team B', 'Team A', 'Team C', 'Team A', 'Team C', 'Team B', 'Team A', 'Team C', 'Team B', 'Team A'] * 2,
        'home_score': [2, 1, 3, 0, 2, 1, 2, 3, 1, 0, 2, 1, 3, 0, 2, 1, 2, 3, 1, 0],
        'away_score': [1, 2, 1, 1, 0, 2, 1, 2, 2, 1, 1, 2, 1, 1, 0, 2, 1, 2, 2, 1],
        'league': ['Premier League'] * 20
    }
    history = pd.DataFrame(data)
    
    # Initialize feature engineer
    feature_engineer = AdvancedFeatureEngineer(history)
    
    # Test form calculation
    form = feature_engineer.calculate_form_points("Team A", datetime(2024, 1, 25), window=5)
    print(f"Form points for Team A: {form:.2f} (expected: 1.0-2.0)")
    assert 0.0 <= form <= 3.0, "Form points out of range"
    
    # Test goal statistics
    goal_stats = feature_engineer.calculate_goal_statistics("Team A", datetime(2024, 1, 25), window=5)
    print(f"Goal stats for Team A: {goal_stats}")
    assert 'goals_for_avg' in goal_stats, "Missing goals_for_avg"
    assert 'clean_sheet_rate' in goal_stats, "Missing clean_sheet_rate"
    
    # Test head-to-head
    h2h = feature_engineer.calculate_head_to_head("Team A", "Team B", datetime(2024, 1, 25))
    print(f"H2H stats for Team A vs Team B: {h2h}")
    assert 'h2h_home_win_rate' in h2h, "Missing h2h_home_win_rate"
    
    # Test streak
    streak = feature_engineer.calculate_streak("Team A", datetime(2024, 1, 25))
    print(f"Streak for Team A: {streak}")
    assert 'streak_len' in streak, "Missing streak_len"
    
    print("✓ Advanced Feature Engineering tests passed!\n")


def test_backtester():
    """Test the enhanced backtester."""
    from app.ml.backtest_enhanced import EnhancedBacktester
    import pandas as pd
    
    print("Testing Enhanced Backtester...")
    
    # Create sample predictions data
    data = {
        'date': pd.date_range(start='2024-01-01', periods=50, freq='D'),
        'model_probability': [0.6, 0.55, 0.65, 0.7, 0.58] * 10,
        'odds': [2.0, 2.1, 1.9, 1.8, 2.05] * 10,
        'result': [1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0],
        'market': ['1X2'] * 50
    }
    predictions_df = pd.DataFrame(data)
    
    # Initialize backtester
    backtester = EnhancedBacktester(initial_bankroll=1000.0, kelly_fraction=0.25)
    
    # Run backtest
    results = backtester.run_backtest(
        predictions_df,
        datetime(2024, 1, 1),
        datetime(2024, 2, 20)
    )
    
    print(f"Backtest results: {results}")
    assert 'total_bets' in results, "Missing total_bets"
    assert 'roi' in results, "Missing roi"
    assert 'sharpe_ratio' in results, "Missing sharpe_ratio"
    
    print(f"Total bets: {results['total_bets']}")
    print(f"ROI: {results['roi']:.2f}%")
    print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
    
    print("✓ Enhanced Backtester tests passed!\n")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing REEDS Mathematical Models")
    print("=" * 60 + "\n")
    
    try:
        test_value_betting_engine()
        test_elo_engine()
        test_advanced_features()
        test_backtester()
        
        print("=" * 60)
        print("✓ All mathematical model tests passed!")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())