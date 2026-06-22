"""Configuration for enhanced mathematical models."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ValueBettingConfig:
    """Configuration for value betting engine."""
    min_edge_threshold: float = 0.02  # Minimum 2% edge
    kelly_fraction: float = 0.25  # Quarter Kelly
    max_stake_fraction: float = 0.25  # Maximum 25% of bankroll
    initial_bankroll: float = 1000.0
    overround_default: float = 0.06  # 6% default overround


@dataclass
class PoissonConfig:
    """Configuration for Poisson model."""
    min_lambda: float = 0.2  # Minimum expected goals
    max_goals_considered: int = 7  # Maximum goals in simulation
    home_advantage: float = 0.3  # Home advantage in goals


@dataclass
class EloConfig:
    """Configuration for Elo rating system."""
    k_factor: float = 32.0  # Elo K-factor
    home_advantage: float = 64.0  # Home advantage in Elo points
    initial_rating: float = 1500.0  # Starting rating for new teams
    draw_margin: float = 0.2  # Base draw probability


@dataclass
class FeatureConfig:
    """Configuration for feature engineering."""
    form_window_short: int = 5  # Short-term form window
    form_window_medium: int = 10  # Medium-term form window
    h2h_max_matches: int = 10  # Maximum H2H matches to consider
    min_matches_for_form: int = 3  # Minimum matches for form calculation


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    train_window_months: int = 6
    test_window_months: int = 1
    walk_forward_steps: int = 6
    min_bets_for_significance: int = 30
    significance_level: float = 0.05


@dataclass
class ModelConfig:
    """Configuration for ML models."""
    ensemble_weights: Dict[str, float] = None
    calibration_method: str = "isotonic"  # isotonic, platt, temperature
    cross_validation_folds: int = 5
    min_training_samples: int = 100
    feature_selection_percentile: int = 80  # Keep top 80% features
    
    def __post_init__(self):
        if self.ensemble_weights is None:
            self.ensemble_weights = {
                "xgboost": 0.4,
                "lightgbm": 0.3,
                "random_forest": 0.2,
                "logistic_regression": 0.1
            }


@dataclass
class RiskManagementConfig:
    """Configuration for risk management."""
    max_daily_loss: float = 0.10  # 10% of bankroll
    max_drawdown: float = 0.20  # 20% from peak
    min_bets_per_day: int = 1
    max_bets_per_day: int = 10
    cooling_off_period_hours: int = 24  # After max loss


# Default configurations
DEFAULT_VALUE_BETTING_CONFIG = ValueBettingConfig()
DEFAULT_POISSON_CONFIG = PoissonConfig()
DEFAULT_ELO_CONFIG = EloConfig()
DEFAULT_FEATURE_CONFIG = FeatureConfig()
DEFAULT_BACKTEST_CONFIG = BacktestConfig()
DEFAULT_MODEL_CONFIG = ModelConfig()
DEFAULT_RISK_MANAGEMENT_CONFIG = RiskManagementConfig()


# Market-specific thresholds
MARKET_THRESHOLDS = {
    "1X2": {"min_confidence": 55, "publish_threshold": 55},
    "Double Chance": {"min_confidence": 58, "publish_threshold": 58},
    "Over/Under 2.5": {"min_confidence": 55, "publish_threshold": 55},
    "Over/Under 1.5": {"min_confidence": 58, "publish_threshold": 58},
    "Over/Under 3.5": {"min_confidence": 58, "publish_threshold": 58},
    "Both Teams to Score": {"min_confidence": 55, "publish_threshold": 55},
    "Correct Score": {"min_confidence": 101, "publish_threshold": 101},  # Never publish by default
    "Spread": {"min_confidence": 60, "publish_threshold": 60},
    "Point Spread": {"min_confidence": 60, "publish_threshold": 60},
}


# Sport-specific configurations
SPORT_CONFIGS = {
    "soccer": {
        "primary_model": "poisson_ensemble",
        "features": ["form", "goals", "h2h", "elo", "streaks"],
        "markets": ["1X2", "Over/Under 2.5", "BTTS", "Double Chance"],
    },
    "basketball": {
        "primary_model": "ml_ensemble",
        "features": ["form", "points", "h2h", "elo", "rest"],
        "markets": ["Moneyline", "Spread", "Total Points"],
    },
    "tennis": {
        "primary_model": "elo_surface",
        "features": ["form", "surface", "h2h", "ranking"],
        "markets": ["Moneyline", "Spread", "Total Games"],
    },
}