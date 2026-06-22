"""Bankroll Management and Survival Protocol for REEDS.

This module implements strict bankroll management rules to ensure long-term
survival and prevent catastrophic losses during variance swings.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from app.db.models import Fixture, Prediction

logger = logging.getLogger(__name__)


class BankrollManager:
    """Manages bankroll allocation and implements survival protocols."""
    
    def __init__(
        self,
        db_session,
        initial_bankroll: float = 1000.0,
        kelly_fraction: float = 0.25,
        max_daily_loss: float = 0.10,
        max_drawdown: float = 0.20,
        high_volatility_cap: float = 0.01
    ):
        """Initialize bankroll manager.
        
        Args:
            db_session: Database session
            initial_bankroll: Starting bankroll amount
            kelly_fraction: Fraction of Kelly to use (0.25 = quarter Kelly)
            max_daily_loss: Maximum loss allowed per day (10% of bankroll)
            max_drawdown: Maximum drawdown from peak (20%)
            high_volatility_cap: Maximum stake for high volatility situations
        """
        self.db = db_session
        self.initial_bankroll = initial_bankroll
        self.kelly_fraction = kelly_fraction
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.high_volatility_cap = high_volatility_cap
        
        # Track current state
        self.current_bankroll = initial_bankroll
        self.peak_bankroll = initial_bankroll
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.utcnow().date()
    
    def calculate_stake(
        self,
        model_probability: float,
        odds: float,
        fixture_context: Optional[Dict] = None
    ) -> float:
        """Calculate optimal stake with survival constraints.
        
        Args:
            model_probability: Model's estimated probability
            odds: Decimal odds
            fixture_context: Optional context (injuries, volatility, etc.)
            
        Returns:
            Stake amount as fraction of bankroll (0-1)
        """
        # Base Kelly calculation
        base_stake = self._kelly_stake(model_probability, odds)
        
        # Apply survival constraints
        stake = self._apply_survival_constraints(base_stake, fixture_context)
        
        return stake
    
    def _kelly_stake(self, model_probability: float, odds: float) -> float:
        """Calculate raw Kelly stake."""
        if odds <= 1.0 or model_probability <= 0.0:
            return 0.0
        
        b = odds - 1  # Net odds
        p = model_probability
        q = 1 - model_probability
        
        kelly = (b * p - q) / b
        kelly *= self.kelly_fraction  # Fractional Kelly
        
        return max(0.0, min(kelly, 0.25))  # Cap at 25%
    
    def _apply_survival_constraints(
        self,
        base_stake: float,
        fixture_context: Optional[Dict] = None
    ) -> float:
        """Apply survival constraints to stake.
        
        Args:
            base_stake: Raw Kelly stake
            fixture_context: Context about fixture (volatility, injuries, etc.)
            
        Returns:
            Constrained stake
        """
        stake = base_stake
        
        # Check daily loss limit
        if self.daily_pnl < -(self.initial_bankroll * self.max_daily_loss):
            logger.warning("Daily loss limit reached. Stopping betting for today.")
            return 0.0
        
        # Check maximum drawdown
        drawdown = (self.peak_bankroll - self.current_bankroll) / self.peak_bankroll
        if drawdown >= self.max_drawdown:
            logger.warning("Maximum drawdown reached. Stopping all betting.")
            return 0.0
        
        # Apply high volatility cap if needed
        if fixture_context and fixture_context.get('high_volatility', False):
            stake = min(stake, self.high_volatility_cap)
            logger.info(f"High volatility detected. Capping stake at {self.high_volatility_cap:.2%}")
        
        # Reduce stake during drawdown
        if drawdown > 0.10:  # If down more than 10%
            reduction_factor = 1.0 - (drawdown - 0.10) * 2  # Reduce by 2x the excess
            stake *= max(0.25, reduction_factor)
            logger.info(f"Reducing stake by {((1 - reduction_factor) * 100):.1f}% due to drawdown")
        
        # Progressive reduction during losing streak
        recent_losses = self._get_recent_loss_streak()
        if recent_losses >= 3:
            stake *= 0.5  # Halve stake after 3 consecutive losses
            logger.warning(f"Lost {recent_losses} in a row. Reducing stake by 50%")
        
        return max(0.0, min(stake, 0.25))  # Final cap at 25%
    
    def _get_recent_loss_streak(self) -> int:
        """Get current losing streak count."""
        # Query last 10 settled predictions
        recent = self.db.query(Prediction, Fixture).join(
            Fixture, Prediction.fixture_id == Fixture.id
        ).filter(
            Prediction.is_published == True,
            Prediction.status == 'active',
            Fixture.home_score != None,
            Fixture.away_score != None
        ).order_by(Fixture.match_date.desc()).limit(10).all()
        
        streak = 0
        for pred, fixture in recent:
            result = self._evaluate_prediction(pred, fixture)
            if result == 0:  # Loss
                streak += 1
            else:
                break
        
        return streak
    
    def _evaluate_prediction(self, pred: Prediction, fixture: Fixture) -> int:
        """Evaluate if prediction won (1) or lost (0)."""
        home_score, away_score = fixture.home_score, fixture.away_score
        pick = pred.pick.lower()
        market = pred.market.lower()
        
        # Simple evaluation (expand as needed)
        if market in {'1x2', 'moneyline'}:
            if 'home' in pick:
                return 1 if home_score > away_score else 0
            elif 'away' in pick:
                return 1 if away_score > home_score else 0
            elif 'draw' in pick:
                return 1 if home_score == away_score else 0
        
        elif market in {'over/under 2.5', 'goals'}:
            total = home_score + away_score
            if 'over' in pick:
                return 1 if total > 2.5 else 0
            elif 'under' in pick:
                return 1 if total < 2.5 else 0
        
        elif market in {'btts', 'both teams to score'}:
            both_scored = home_score > 0 and away_score > 0
            if 'yes' in pick:
                return 1 if both_scored else 0
            elif 'no' in pick:
                return 0 if both_scored else 1
        
        return -1  # Unable to evaluate
    
    def update_bankroll(self, pnl: float):
        """Update bankroll after bet settlement.
        
        Args:
            pnl: Profit/loss amount
        """
        self.current_bankroll += pnl
        self.daily_pnl += pnl
        
        # Update peak if needed
        if self.current_bankroll > self.peak_bankroll:
            self.peak_bankroll = self.current_bankroll
        
        # Reset daily PnL if it's a new day
        today = datetime.utcnow().date()
        if today != self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today
    
    def get_status(self) -> Dict[str, any]:
        """Get current bankroll status and constraints.
        
        Returns:
            Dictionary with current status
        """
        drawdown = (self.peak_bankroll - self.current_bankroll) / self.peak_bankroll
        daily_loss_pct = self.daily_pnl / self.initial_bankroll
        
        return {
            'current_bankroll': round(self.current_bankroll, 2),
            'initial_bankroll': self.initial_bankroll,
            'peak_bankroll': round(self.peak_bankroll, 2),
            'total_profit': round(self.current_bankroll - self.initial_bankroll, 2),
            'total_return_pct': round(((self.current_bankroll - self.initial_bankroll) / self.initial_bankroll) * 100, 2),
            'drawdown_pct': round(drawdown * 100, 2),
            'daily_pnl': round(self.daily_pnl, 2),
            'daily_pnl_pct': round(daily_loss_pct * 100, 2),
            'constraints': {
                'daily_loss_limit_reached': daily_loss_pct <= -self.max_daily_loss,
                'max_drawdown_reached': drawdown >= self.max_drawdown,
                'current_loss_streak': self._get_recent_loss_streak(),
                'betting_allowed': (
                    daily_loss_pct > -self.max_daily_loss and
                    drawdown < self.max_drawdown
                )
            }
        }
    
    def should_allow_betting(self) -> Tuple[bool, str]:
        """Check if betting should be allowed.
        
        Returns:
            Tuple of (allowed, reason)
        """
        status = self.get_status()
        constraints = status['constraints']
        
        if constraints['daily_loss_limit_reached']:
            return False, "Daily loss limit reached"
        
        if constraints['max_drawdown_reached']:
            return False, "Maximum drawdown reached"
        
        if constraints['current_loss_streak'] >= 5:
            return False, f"Lost {constraints['current_loss_streak']} in a row"
        
        return True, "Betting allowed"
    
    def identify_high_volatility_situations(
        self,
        fixture: Dict,
        model_factors: List[Dict]
    ) -> bool:
        """Identify if a fixture has high volatility risk.
        
        Args:
            fixture: Fixture data
            model_factors: Model factors from prediction
            
        Returns:
            True if high volatility detected
        """
        volatility_factors = 0
        
        # Check for derby matches (local rivals)
        if self._is_derby_match(fixture.get('home_team'), fixture.get('away_team')):
            volatility_factors += 1
        
        # Check for cup matches (more unpredictable)
        if 'cup' in fixture.get('league', '').lower() or 'final' in fixture.get('league', '').lower():
            volatility_factors += 1
        
        # Check for teams with high scoring variance
        for factor in model_factors:
            if 'scoring_consistency' in factor.get('label', '').lower():
                consistency = factor.get('value', 0.5)
                if isinstance(consistency, str):
                    try:
                        consistency = float(consistency.replace('%', '')) / 100
                    except:
                        consistency = 0.5
                if consistency < 0.4:  # Low consistency = high variance
                    volatility_factors += 1
        
        # Check for extreme odds (long shots)
        home_odds = fixture.get('home_odds', 2.0)
        away_odds = fixture.get('away_odds', 2.0)
        if home_odds > 5.0 or away_odds > 5.0:
            volatility_factors += 1
        
        return volatility_factors >= 2  # High volatility if 2+ factors
    
    def _is_derby_match(self, home_team: str, away_team: str) -> bool:
        """Check if match is a local derby."""
        # This would need a database of local rivalries
        # For now, return False
        return False
    
    def log_bet(
        self,
        prediction: Prediction,
        stake: float,
        odds: float,
        expected_value: float
    ):
        """Log a bet for tracking and analysis.
        
        Args:
            prediction: Prediction object
            stake: Stake amount
            odds: Odds received
            expected_value: Expected value of bet
        """
        from app.db.models import BetLog
        
        bet_log = BetLog(
            prediction_id=prediction.id,
            stake_fraction=stake,
            odds=odds,
            expected_value=expected_value,
            bankroll_before=self.current_bankroll,
            kelly_fraction=self.kelly_fraction,
            constraints_applied=self.get_status()['constraints']
        )
        
        self.db.add(bet_log)
        self.db.commit()