"""Value Betting Engine - Mathematical foundation for +EV predictions.

This module implements the core mathematical models for identifying value bets
by comparing model probabilities against bookmaker implied probabilities.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValueBet:
    """Represents a value betting opportunity."""
    market: str
    pick: str
    model_probability: float
    bookmaker_odds: float
    implied_probability: float
    edge: float  # Model prob - implied prob
    kelly_stake: float  # Fraction of bankroll to stake
    confidence: str  # High/Medium/Low based on edge size
    expected_value: float  # (prob * (odds - 1)) - (1 - prob)


class ValueBettingEngine:
    """Mathematical engine for identifying +EV betting opportunities.
    
    Implements:
    - Implied probability calculation from odds
    - Value detection (model prob > implied prob)
    - Kelly Criterion for optimal staking
    - ROI and CLV tracking
    """
    
    def __init__(self, bankroll: float = 1000.0, kelly_fraction: float = 0.25):
        """Initialize the value betting engine.
        
        Args:
            bankroll: Starting bankroll amount
            kelly_fraction: Fraction of Kelly to use (0.25 = quarter Kelly)
        """
        self.bankroll = bankroll
        self.kelly_fraction = kelly_fraction
        
    def implied_probability(self, odds: float, market_type: str = "1x2") -> float:
        """Calculate implied probability from bookmaker odds.
        
        Args:
            odds: Decimal odds from bookmaker
            market_type: Type of market (affects overround calculation)
            
        Returns:
            Implied probability as a decimal (0-1)
        """
        if odds <= 1.0:
            return 1.0
            
        # For 1X2 markets, we need to account for overround
        if market_type == "1x2":
            # Typical overround is 5-8%, we'll use 6% as default
            overround = 0.06
            fair_odds = odds * (1 - overround)
            return 1.0 / fair_odds
        else:
            # For other markets (totals, BTTS), overround is typically lower
            return 1.0 / odds
    
    def calculate_edge(self, model_prob: float, bookmaker_odds: float) -> float:
        """Calculate the edge (value) of a bet.
        
        Edge = Model Probability - Implied Probability
        
        Positive edge indicates value bet.
        
        Args:
            model_prob: Model's estimated probability (0-1)
            bookmaker_odds: Bookmaker's decimal odds
            
        Returns:
            Edge as a decimal (e.g., 0.05 = 5% edge)
        """
        implied_prob = self.implied_probability(bookmaker_odds)
        return model_prob - implied_prob
    
    def expected_value(self, model_prob: float, odds: float) -> float:
        """Calculate expected value of a bet.
        
        EV = (Probability * (Odds - 1)) - (1 - Probability)
        
        Positive EV indicates profitable long-term bet.
        
        Args:
            model_prob: Model's estimated probability (0-1)
            odds: Decimal odds
            
        Returns:
            Expected value as decimal (e.g., 0.10 = 10% ROI)
        """
        return (model_prob * (odds - 1)) - (1 - model_prob)
    
    def kelly_criterion(self, model_prob: float, odds: float) -> float:
        """Calculate optimal stake using Kelly Criterion.
        
        Kelly % = (BP - Q) / B
        Where:
        - B = Decimal odds - 1
        - P = Probability of winning (model prob)
        - Q = Probability of losing (1 - P)
        
        Args:
            model_prob: Model's estimated probability (0-1)
            odds: Decimal odds
            
        Returns:
            Fraction of bankroll to stake (0-1)
        """
        if odds <= 1.0 or model_prob <= 0.0:
            return 0.0
            
        b = odds - 1  # Net odds received
        p = model_prob
        q = 1 - model_prob  # Probability of losing
        
        kelly = (b * p - q) / b
        
        # Apply fractional Kelly (typically 1/4 or 1/2 Kelly for risk management)
        kelly *= self.kelly_fraction
        
        # Clamp to reasonable range (0-25% of bankroll)
        return max(0.0, min(kelly, 0.25))
    
    def identify_value_bets(
        self,
        predictions: List[Dict],
        fixture_odds: Dict[str, float]
    ) -> List[ValueBet]:
        """Identify value betting opportunities from predictions.
        
        Args:
            predictions: List of prediction items with market, pick, confidence
            fixture_odds: Dictionary mapping picks to bookmaker odds
            
        Returns:
            List of ValueBet objects for opportunities with positive edge
        """
        value_bets = []
        
        for pred in predictions:
            market = pred.get("market", "")
            pick = pred.get("pick", "")
            confidence = pred.get("confidence", 0) / 100.0  # Convert to 0-1
            
            # Get bookmaker odds for this pick
            odds_key = f"{market}_{pick}".replace(" ", "_").lower()
            bookmaker_odds = fixture_odds.get(odds_key)
            
            if not bookmaker_odds or bookmaker_odds <= 1.0:
                continue
            
            # Calculate implied probability from odds
            implied_prob = self.implied_probability(bookmaker_odds, market)
            
            # For now, use confidence as model probability (will be enhanced later)
            model_prob = confidence
            
            # Calculate edge
            edge = self.calculate_edge(model_prob, bookmaker_odds)
            
            # Only consider positive edge (value bets)
            if edge > 0.02:  # Minimum 2% edge threshold
                ev = self.expected_value(model_prob, bookmaker_odds)
                kelly_stake = self.kelly_criterion(model_prob, bookmaker_odds)
                
                # Determine confidence level based on edge size
                if edge >= 0.10:
                    conf_level = "High"
                elif edge >= 0.05:
                    conf_level = "Medium"
                else:
                    conf_level = "Low"
                
                value_bet = ValueBet(
                    market=market,
                    pick=pick,
                    model_probability=model_prob,
                    bookmaker_odds=bookmaker_odds,
                    implied_probability=implied_prob,
                    edge=edge,
                    kelly_stake=kelly_stake,
                    confidence=conf_level,
                    expected_value=ev
                )
                value_bets.append(value_bet)
        
        # Sort by edge (highest value first)
        value_bets.sort(key=lambda x: x.edge, reverse=True)
        
        return value_bets
    
    def calculate_clv(self, opening_odds: float, closing_odds: float) -> float:
        """Calculate Closing Line Value (CLV).
        
        CLV measures how much value was captured compared to closing line.
        Positive CLV indicates you got better odds than the market closed at.
        
        Args:
            opening_odds: Odds when bet was placed
            closing_odds: Odds at market close
            
        Returns:
            CLV as percentage (positive = good value)
        """
        if closing_odds <= 1.0 or opening_odds <= 1.0:
            return 0.0
            
        # Convert odds to implied probabilities
        opening_prob = 1.0 / opening_odds
        closing_prob = 1.0 / closing_odds
        
        # CLV = (Closing Prob - Opening Prob) / Opening Prob
        clv = (closing_prob - opening_prob) / opening_prob
        
        return clv * 100  # Return as percentage
    
    def track_performance(
        self,
        bets: List[ValueBet],
        results: List[bool]
    ) -> Dict[str, float]:
        """Track betting performance metrics.
        
        Args:
            bets: List of ValueBet objects that were placed
            results: List of boolean results (True = won, False = lost)
            
        Returns:
            Dictionary of performance metrics
        """
        if len(bets) != len(results):
            raise ValueError("Bets and results must have same length")
        
        total_staked = 0.0
        total_return = 0.0
        wins = 0
        losses = 0
        
        for bet, result in zip(bets, results):
            stake = bet.kelly_stake * self.bankroll
            total_staked += stake
            
            if result:
                wins += 1
                total_return += stake * bet.bookmaker_odds
            else:
                losses += 1
        
        roi = ((total_return - total_staked) / total_staked * 100) if total_staked > 0 else 0
        hit_rate = (wins / len(bets) * 100) if bets else 0
        yield_pct = (total_return - total_staked) / total_staked * 100 if total_staked > 0 else 0
        
        return {
            "total_bets": len(bets),
            "wins": wins,
            "losses": losses,
            "hit_rate": round(hit_rate, 2),
            "total_staked": round(total_staked, 2),
            "total_return": round(total_return, 2),
            "profit": round(total_return - total_staked, 2),
            "roi": round(roi, 2),
            "yield": round(yield_pct, 2)
        }


class PoissonValueEngine(ValueBettingEngine):
    """Enhanced value engine using Poisson distributions for goal-based markets."""
    
    def __init__(self, bankroll: float = 1000.0, kelly_fraction: float = 0.25):
        super().__init__(bankroll, kelly_fraction)
    
    def poisson_probability(self, lam: float, goals: int) -> float:
        """Calculate Poisson probability for exact number of goals.
        
        P(X = k) = (λ^k * e^-λ) / k!
        
        Args:
            lam: Expected goals (λ)
            goals: Number of goals to calculate probability for
            
        Returns:
            Probability as decimal (0-1)
        """
        from math import exp, factorial
        
        if goals < 0:
            return 0.0
            
        return (lam ** goals) * exp(-lam) / factorial(goals)
    
    def calculate_over_under_prob(
        self,
        home_expected: float,
        away_expected: float,
        line: float
    ) -> float:
        """Calculate probability of over/under a goal line.
        
        Uses convolution of two Poisson distributions (home + away goals).
        
        Args:
            home_expected: Expected goals for home team
            away_expected: Expected goals for away team
            line: Goal line (e.g., 2.5)
            
        Returns:
            Probability of over the line
        """
        total_expected = home_expected + away_expected
        
        # Calculate probability of under (sum of probabilities for all outcomes <= line)
        under_prob = 0.0
        max_goals = int(line) + 5  # Calculate up to line + 5 goals
        
        for total_goals in range(0, max_goals + 1):
            if total_goals <= line:
                # Convolution of two Poisson distributions
                prob = 0.0
                for home_goals in range(0, total_goals + 1):
                    away_goals = total_goals - home_goals
                    home_prob = self.poisson_probability(home_expected, home_goals)
                    away_prob = self.poisson_probability(away_expected, away_goals)
                    prob += home_prob * away_prob
                under_prob += prob
        
        return 1.0 - under_prob  # Probability of over
    
    def calculate_btts_prob(
        self,
        home_expected: float,
        away_expected: float
    ) -> float:
        """Calculate probability of both teams to score.
        
        BTTS = 1 - P(Home fails to score) - P(Away fails to score) + P(Both fail)
        
        Args:
            home_expected: Expected goals for home team
            away_expected: Expected goals for away team
            
        Returns:
            Probability of BTTS
        """
        # P(Team fails to score) = Poisson(0, λ)
        home_fail = self.poisson_probability(home_expected, 0)
        away_fail = self.poisson_probability(away_expected, 0)
        
        # P(Both fail) = P(Home fails) * P(Away fails) (assuming independence)
        both_fail = home_fail * away_fail
        
        # P(BTTS) = 1 - P(At least one fails) = 1 - (P(Home fails) + P(Away fails) - P(Both fail))
        btts_prob = 1.0 - (home_fail + away_fail - both_fail)
        
        return btts_prob


class EloValueEngine(ValueBettingEngine):
    """Value engine incorporating Elo ratings for team strength."""
    
    def __init__(self, bankroll: float = 1000.0, kelly_fraction: float = 0.25, 
                 elo_k_factor: float = 32.0):
        super().__init__(bankroll, kelly_fraction)
        self.elo_k_factor = elo_k_factor
        self.elo_ratings: Dict[str, float] = {}
    
    def set_elo_rating(self, team: str, rating: float):
        """Set Elo rating for a team."""
        self.elo_ratings[team] = rating
    
    def get_elo_rating(self, team: str) -> float:
        """Get Elo rating for a team, defaulting to 1500 if unknown."""
        return self.elo_ratings.get(team, 1500.0)
    
    def expected_score(self, rating_a: float, rating_b: float) -> float:
        """Calculate expected score for player A based on Elo ratings.
        
        E(A) = 1 / (1 + 10^((Rb - Ra) / 400))
        
        Args:
            rating_a: Elo rating of player A
            rating_b: Elo rating of player B
            
        Returns:
            Expected score (probability of A winning)
        """
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))
    
    def update_elo(
        self,
        team: str,
        opponent_rating: float,
        actual_score: float,
        home_advantage: float = 0.0
    ) -> float:
        """Update Elo rating after a match.
        
        New Rating = Old Rating + K * (Actual Score - Expected Score)
        
        Args:
            team: Team name
            opponent_rating: Opponent's Elo rating
            actual_score: Actual result (1 = win, 0.5 = draw, 0 = loss)
            home_advantage: Additional rating points for home team
            
        Returns:
            New Elo rating
        """
        current_rating = self.get_elo_rating(team)
        
        # Adjust for home advantage
        if home_advantage > 0:
            adjusted_rating = current_rating + home_advantage
        else:
            adjusted_rating = current_rating
        
        expected_score = self.expected_score(adjusted_rating, opponent_rating)
        rating_change = self.elo_k_factor * (actual_score - expected_score)
        
        new_rating = current_rating + rating_change
        self.elo_ratings[team] = new_rating
        
        return new_rating
    
    def predict_from_elo(
        self,
        home_team: str,
        away_team: str,
        home_advantage: float = 64.0
    ) -> Dict[str, float]:
        """Predict match outcome probabilities from Elo ratings.
        
        Args:
            home_team: Home team name
            away_team: Away team name
            home_advantage: Elo points added to home team (default ~64)
            
        Returns:
            Dictionary with probabilities for home_win, draw, away_win
        """
        home_elo = self.get_elo_rating(home_team) + home_advantage
        away_elo = self.get_elo_rating(away_team)
        
        # Calculate expected scores
        home_expected = self.expected_score(home_elo, away_elo)
        away_expected = self.expected_score(away_elo, home_elo)
        
        # Draw probability (simplified model)
        # In reality, this would need more sophisticated modeling
        draw_margin = 0.2  # Base draw probability
        draw_prob = draw_margin * (1.0 - abs(home_expected - away_expected))
        
        # Normalize to ensure probabilities sum to 1
        home_prob = home_expected * (1.0 - draw_prob)
        away_prob = away_expected * (1.0 - draw_prob)
        
        # Ensure they sum to 1
        total = home_prob + away_prob + draw_prob
        home_prob /= total
        away_prob /= total
        draw_prob /= total
        
        return {
            "home_win": home_prob,
            "draw": draw_prob,
            "away_win": away_prob
        }