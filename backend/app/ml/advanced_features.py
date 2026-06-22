"""Advanced feature engineering for sports predictions.

This module implements comprehensive feature engineering including:
- Form analysis with rolling windows
- Head-to-head statistics
- Player availability indices
- Tactical matchup scores
- Market inefficiency signals
- Situational motivation factors
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class AdvancedFeatureEngineer:
    """Comprehensive feature engineering for match predictions."""
    
    def __init__(self, history: pd.DataFrame):
        """Initialize with historical data.
        
        Args:
            history: DataFrame with historical match data
        """
        self.history = history
        self._validate_history()
    
    def _validate_history(self):
        """Validate that history DataFrame has required columns."""
        required_columns = [
            'match_date', 'home_team', 'away_team', 
            'home_score', 'away_score'
        ]
        missing = set(required_columns) - set(self.history.columns)
        if missing:
            logger.warning(f"Missing columns in history: {missing}")
    
    def calculate_form_points(
        self,
        team: str,
        match_date: datetime,
        window: int = 5,
        home_away: str = 'all'
    ) -> float:
        """Calculate recent form points for a team.
        
        Args:
            team: Team name
            match_date: Date of the upcoming match
            window: Number of matches to consider
            home_away: 'home', 'away', or 'all'
            
        Returns:
            Points per match (0-3 scale)
        """
        # Filter matches before the target date
        mask = (
            (self.history['match_date'] < match_date) &
            ((self.history['home_team'] == team) | (self.history['away_team'] == team))
        )
        
        if home_away == 'home':
            mask = mask & (self.history['home_team'] == team)
        elif home_away == 'away':
            mask = mask & (self.history['away_team'] == team)
        
        recent_matches = self.history[mask].sort_values('match_date', ascending=False).head(window)
        
        if recent_matches.empty:
            return 1.0  # Default to average form
        
        points = []
        for _, match in recent_matches.iterrows():
            is_home = match['home_team'] == team
            team_score = match['home_score'] if is_home else match['away_score']
            opp_score = match['away_score'] if is_home else match['home_score']
            
            if team_score > opp_score:
                points.append(3)
            elif team_score == opp_score:
                points.append(1)
            else:
                points.append(0)
        
        return sum(points) / len(points) if points else 1.0
    
    def calculate_goal_statistics(
        self,
        team: str,
        match_date: datetime,
        window: int = 10,
        home_away: str = 'all'
    ) -> Dict[str, float]:
        """Calculate goal statistics for a team.
        
        Returns:
            Dictionary with goals_for_avg, goals_against_avg, clean_sheet_rate, etc.
        """
        mask = (
            (self.history['match_date'] < match_date) &
            ((self.history['home_team'] == team) | (self.history['away_team'] == team))
        )
        
        if home_away == 'home':
            mask = mask & (self.history['home_team'] == team)
        elif home_away == 'away':
            mask = mask & (self.history['away_team'] == team)
        
        recent_matches = self.history[mask].sort_values('match_date', ascending=False).head(window)
        
        if recent_matches.empty:
            return {
                'goals_for_avg': 1.0,
                'goals_against_avg': 1.0,
                'clean_sheet_rate': 0.3,
                'failed_to_score_rate': 0.3,
                'over_2_5_rate': 0.5,
                'btts_rate': 0.5
            }
        
        goals_for = []
        goals_against = []
        clean_sheets = 0
        failed_to_score = 0
        over_2_5 = 0
        btts = 0
        
        for _, match in recent_matches.iterrows():
            is_home = match['home_team'] == team
            team_score = match['home_score'] if is_home else match['away_score']
            opp_score = match['away_score'] if is_home else match['home_score']
            
            goals_for.append(team_score)
            goals_against.append(opp_score)
            
            if team_score == 0:
                failed_to_score += 1
            if opp_score == 0:
                clean_sheets += 1
            
            total_goals = team_score + opp_score
            if total_goals > 2.5:
                over_2_5 += 1
            if team_score > 0 and opp_score > 0:
                btts += 1
        
        n_matches = len(recent_matches)
        return {
            'goals_for_avg': sum(goals_for) / n_matches,
            'goals_against_avg': sum(goals_against) / n_matches,
            'clean_sheet_rate': clean_sheets / n_matches,
            'failed_to_score_rate': failed_to_score / n_matches,
            'over_2_5_rate': over_2_5 / n_matches,
            'btts_rate': btts / n_matches
        }
    
    def calculate_head_to_head(
        self,
        home_team: str,
        away_team: str,
        match_date: datetime,
        max_matches: int = 10
    ) -> Dict[str, float]:
        """Calculate head-to-head statistics between two teams.
        
        Returns:
            Dictionary with h2h statistics
        """
        mask = (
            (self.history['match_date'] < match_date) &
            (((self.history['home_team'] == home_team) & (self.history['away_team'] == away_team)) |
             ((self.history['home_team'] == away_team) & (self.history['away_team'] == home_team)))
        )
        
        h2h_matches = self.history[mask].sort_values('match_date', ascending=False).head(max_matches)
        
        if h2h_matches.empty:
            return {
                'h2h_home_win_rate': 0.33,
                'h2h_draw_rate': 0.34,
                'h2h_away_win_rate': 0.33,
                'h2h_avg_goals': 2.5
            }
        
        home_wins = 0
        away_wins = 0
        draws = 0
        total_goals = 0
        
        for _, match in h2h_matches.iterrows():
            home_score = match['home_score']
            away_score = match['away_score']
            total_goals += home_score + away_score
            
            if home_score > away_score:
                if match['home_team'] == home_team:
                    home_wins += 1
                else:
                    away_wins += 1
            elif home_score < away_score:
                if match['home_team'] == home_team:
                    away_wins += 1
                else:
                    home_wins += 1
            else:
                draws += 1
        
        n_matches = len(h2h_matches)
        return {
            'h2h_home_win_rate': home_wins / n_matches,
            'h2h_draw_rate': draws / n_matches,
            'h2h_away_win_rate': away_wins / n_matches,
            'h2h_avg_goals': total_goals / n_matches
        }
    
    def calculate_streak(
        self,
        team: str,
        match_date: datetime,
        window: int = 10
    ) -> Dict[str, any]:
        """Calculate current streak for a team.
        
        Returns:
            Dictionary with streak length, type (W/L/D), and whether winning
        """
        mask = (
            (self.history['match_date'] < match_date) &
            ((self.history['home_team'] == team) | (self.history['away_team'] == team))
        )
        
        recent_matches = self.history[mask].sort_values('match_date', ascending=False).head(window)
        
        if recent_matches.empty:
            return {'streak_len': 0, 'streak_type': 'N', 'streak_winning': False}
        
        streak_len = 0
        streak_type = None
        
        for _, match in recent_matches.iterrows():
            is_home = match['home_team'] == team
            team_score = match['home_score'] if is_home else match['away_score']
            opp_score = match['away_score'] if is_home else match['home_score']
            
            if team_score > opp_score:
                current_result = 'W'
            elif team_score < opp_score:
                current_result = 'L'
            else:
                current_result = 'D'
            
            if streak_type is None:
                streak_type = current_result
                streak_len = 1
            elif current_result == streak_type:
                streak_len += 1
            else:
                break
        
        return {
            'streak_len': streak_len,
            'streak_type': streak_type,
            'streak_winning': streak_type == 'W' if streak_type else False
        }
    
    def calculate_league_strength(
        self,
        league: str,
        match_date: datetime
    ) -> float:
        """Calculate league strength coefficient.
        
        This could be based on UEFA coefficients, average goals, competitiveness, etc.
        For now, we'll use a simple approach based on goal variance.
        
        Returns:
            League strength coefficient (0-1 scale)
        """
        league_matches = self.history[
            (self.history['league'] == league) & 
            (self.history['match_date'] < match_date)
        ]
        
        if league_matches.empty:
            return 0.5  # Default to average
        
        # Calculate goal variance as a proxy for league quality
        goals = league_matches['home_score'] + league_matches['away_score']
        avg_goals = goals.mean()
        goal_variance = goals.var()
        
        # Normalize to 0-1 scale (higher variance = more unpredictable = lower quality)
        # This is a simplified metric
        strength = 1.0 / (1.0 + goal_variance / avg_goals)
        
        return min(max(strength, 0.0), 1.0)
    
    def calculate_rest_days(
        self,
        team: str,
        match_date: datetime
    ) -> int:
        """Calculate days of rest since last match.
        
        Returns:
            Number of rest days
        """
        team_matches = self.history[
            ((self.history['home_team'] == team) | (self.history['away_team'] == team)) &
            (self.history['match_date'] < match_date)
        ].sort_values('match_date', ascending=False)
        
        if team_matches.empty:
            return 7  # Default to a week rest
        
        last_match_date = team_matches.iloc[0]['match_date']
        rest_days = (match_date - last_match_date).days
        
        return max(rest_days, 0)
    
    def generate_match_features(
        self,
        home_team: str,
        away_team: str,
        match_date: datetime,
        league: str,
        home_odds: Optional[float] = None,
        draw_odds: Optional[float] = None,
        away_odds: Optional[float] = None
    ) -> Dict[str, float]:
        """Generate comprehensive feature set for a match.
        
        This is the main method that combines all features into a single dictionary.
        
        Returns:
            Dictionary of features for machine learning model
        """
        features = {}
        
        # Form features
        features['home_form_points'] = self.calculate_form_points(home_team, match_date, 5, 'home')
        features['away_form_points'] = self.calculate_form_points(away_team, match_date, 5, 'away')
        features['home_form_points_all'] = self.calculate_form_points(home_team, match_date, 5, 'all')
        features['away_form_points_all'] = self.calculate_form_points(away_team, match_date, 5, 'all')
        
        # Goal statistics
        home_goals = self.calculate_goal_statistics(home_team, match_date, 10, 'home')
        away_goals = self.calculate_goal_statistics(away_team, match_date, 10, 'away')
        
        features['home_goals_for'] = home_goals['goals_for_avg']
        features['home_goals_against'] = home_goals['goals_against_avg']
        features['away_goals_for'] = away_goals['goals_for_avg']
        features['away_goals_against'] = away_goals['goals_against_avg']
        
        features['home_clean_sheet_rate'] = home_goals['clean_sheet_rate']
        features['away_clean_sheet_rate'] = away_goals['clean_sheet_rate']
        features['home_failed_score_rate'] = home_goals['failed_to_score_rate']
        features['away_failed_score_rate'] = away_goals['failed_to_score_rate']
        
        # Head-to-head
        h2h = self.calculate_head_to_head(home_team, away_team, match_date)
        features['h2h_home_win_rate'] = h2h['h2h_home_win_rate']
        features['h2h_draw_rate'] = h2h['h2h_draw_rate']
        features['h2h_away_win_rate'] = h2h['h2h_away_win_rate']
        features['h2h_avg_goals'] = h2h['h2h_avg_goals']
        
        # Streaks
        home_streak = self.calculate_streak(home_team, match_date)
        away_streak = self.calculate_streak(away_team, match_date)
        
        features['home_streak_len'] = home_streak['streak_len']
        features['home_streak_winning'] = 1.0 if home_streak['streak_winning'] else 0.0
        features['away_streak_len'] = away_streak['streak_len']
        features['away_streak_winning'] = 1.0 if away_streak['streak_winning'] else 0.0
        
        # League strength
        features['league_strength'] = self.calculate_league_strength(league, match_date)
        
        # Rest days
        features['home_rest_days'] = self.calculate_rest_days(home_team, match_date)
        features['away_rest_days'] = self.calculate_rest_days(away_team, match_date)
        
        # Odds-based features (if available)
        if home_odds and away_odds:
            features['home_odds_implied_prob'] = 1.0 / home_odds
            features['away_odds_implied_prob'] = 1.0 / away_odds
            features['draw_odds_implied_prob'] = 1.0 / draw_odds if draw_odds else 0.0
            
            # Market efficiency features
            total_implied = (features['home_odds_implied_prob'] + 
                           features['draw_odds_implied_prob'] + 
                           features['away_odds_implied_prob'])
            features['market_overround'] = total_implied - 1.0
            
            # Favorite detection
            if home_odds < away_odds:
                features['is_home_favorite'] = 1.0
                features['favorite_odds'] = home_odds
            else:
                features['is_home_favorite'] = 0.0
                features['favorite_odds'] = away_odds
        
        # Derived features
        features['form_differential'] = features['home_form_points'] - features['away_form_points']
        features['goals_for_differential'] = features['home_goals_for'] - features['away_goals_against']
        features['goals_against_differential'] = features['home_goals_against'] - features['away_goals_for']
        
        return features