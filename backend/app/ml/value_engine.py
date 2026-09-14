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
        self.bankroll = bankroll
        self.kelly_fraction = kelly_fraction
        
    def implied_probability(self, odds: float, market_type: str = "1x2") -> float:
        if odds <= 1.0:
            return 1.0
        if market_type.lower() in ("1x2", "1x2"):
            overround = 0.06
            fair_odds = odds * (1 - overround)
            return 1.0 / fair_odds
        return 1.0 / odds
    
    def calculate_edge(self, model_prob: float, bookmaker_odds: float) -> float:
        implied_prob = self.implied_probability(bookmaker_odds)
        return model_prob - implied_prob
    
    def expected_value(self, model_prob: float, odds: float) -> float:
        return (model_prob * (odds - 1)) - (1 - model_prob)
    
    def kelly_criterion(self, model_prob: float, odds: float) -> float:
        if odds <= 1.0 or model_prob <= 0.0:
            return 0.0
        b = odds - 1
        p = model_prob
        q = 1 - model_prob
        kelly = (b * p - q) / b
        kelly *= self.kelly_fraction
        return max(0.0, min(kelly, 0.25))
    
    def identify_value_bets(
        self,
        predictions: List[Dict],
        fixture_odds: Dict[str, float]
    ) -> List[ValueBet]:
        """Identify value betting opportunities from predictions.

        Prefer explicit ``model_probability`` (calibrated) over raw confidence.
        """
        value_bets = []
        
        for pred in predictions:
            market = pred.get("market", "")
            pick = pred.get("pick", "")
            # Prefer calibrated model_probability when the engine supplies it
            if "model_probability" in pred and pred["model_probability"] is not None:
                model_prob = float(pred["model_probability"])
            else:
                model_prob = float(pred.get("confidence", 0)) / 100.0

            odds_key = f"{market}_{pick}".replace(" ", "_").lower()
            bookmaker_odds = fixture_odds.get(odds_key)
            
            if not bookmaker_odds or bookmaker_odds <= 1.0:
                continue
            
            implied_prob = self.implied_probability(bookmaker_odds, market)
            edge = self.calculate_edge(model_prob, bookmaker_odds)
            
            if edge > 0.02:  # Minimum 2% edge threshold
                ev = self.expected_value(model_prob, bookmaker_odds)
                kelly_stake = self.kelly_criterion(model_prob, bookmaker_odds)
                
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
        
        value_bets.sort(key=lambda x: x.edge, reverse=True)
        return value_bets
    
    def calculate_clv(self, opening_odds: float, closing_odds: float) -> float:
        if closing_odds <= 1.0 or opening_odds <= 1.0:
            return 0.0
        opening_prob = 1.0 / opening_odds
        closing_prob = 1.0 / closing_odds
        clv = (closing_prob - opening_prob) / opening_prob
        return clv * 100
    
    def track_performance(
        self,
        bets: List[ValueBet],
        results: List[bool]
    ) -> Dict[str, float]:
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
        under_prob = 0.0
        max_goals = int(line) + 5
        for total_goals in range(0, max_goals + 1):
            if total_goals <= line:
                prob = 0.0
                for home_goals in range(0, total_goals + 1):
                    away_goals = total_goals - home_goals
                    home_prob = self.poisson_probability(home_expected, home_goals)
                    away_prob = self.poisson_probability(away_expected, away_goals)
                    prob += home_prob * away_prob
                under_prob += prob
        return 1.0 - under_prob
    
    def calculate_btts_prob(
        self,
        home_expected: float,
        away_expected: float
    ) -> float:
        home_fail = self.poisson_probability(home_expected, 0)
        away_fail = self.poisson_probability(away_expected, 0)
        both_fail = home_fail * away_fail
        return 1.0 - (home_fail + away_fail - both_fail)


class EloValueEngine(ValueBettingEngine):
    """Value engine incorporating Elo ratings for team strength."""
    
    def __init__(self, bankroll: float = 1000.0, kelly_fraction: float = 0.25, 
                 elo_k_factor: float = 32.0):
        super().__init__(bankroll, kelly_fraction)
        self.elo_k_factor = elo_k_factor
        self.elo_ratings: Dict[str, float] = {}
    
    def set_elo_rating(self, team: str, rating: float):
        self.elo_ratings[team] = rating
    
    def get_elo_rating(self, team: str) -> float:
        return self.elo_ratings.get(team, 1500.0)
    
    def expected_score(self, rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))
    
    def update_elo(
        self,
        team: str,
        opponent_rating: float,
        actual_score: float,
        home_advantage: float = 0.0
    ) -> float:
        current_rating = self.get_elo_rating(team)
        adjusted_rating = current_rating + home_advantage if home_advantage > 0 else current_rating
        expected = self.expected_score(adjusted_rating, opponent_rating)
        rating_change = self.elo_k_factor * (actual_score - expected)
        new_rating = current_rating + rating_change
        self.elo_ratings[team] = new_rating
        return new_rating
    
    def predict_from_elo(
        self,
        home_team: str,
        away_team: str,
        home_advantage: float = 64.0
    ) -> Dict[str, float]:
        home_elo = self.get_elo_rating(home_team) + home_advantage
        away_elo = self.get_elo_rating(away_team)
        home_expected = self.expected_score(home_elo, away_elo)
        away_expected = self.expected_score(away_elo, home_elo)
        draw_margin = 0.2
        draw_prob = draw_margin * (1.0 - abs(home_expected - away_expected))
        home_prob = home_expected * (1.0 - draw_prob)
        away_prob = away_expected * (1.0 - draw_prob)
        total = home_prob + away_prob + draw_prob
        home_prob /= total
        away_prob /= total
        draw_prob /= total
        return {
            "home_win": home_prob,
            "draw": draw_prob,
            "away_win": away_prob
        }
