"""Enhanced backtesting module for model validation.

This module provides comprehensive backtesting capabilities including:
- Walk-forward validation
- ROI and yield tracking
- Kelly Criterion performance
- Market efficiency analysis
- Statistical significance testing
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


class EnhancedBacktester:
    """Comprehensive backtesting engine for sports prediction models."""
    
    def __init__(self, initial_bankroll: float = 1000.0, kelly_fraction: float = 0.25):
        """Initialize backtester.
        
        Args:
            initial_bankroll: Starting bankroll amount
            kelly_fraction: Fraction of Kelly to use for staking
        """
        self.initial_bankroll = initial_bankroll
        self.kelly_fraction = kelly_fraction
        self.results_history = []
    
    def calculate_implied_probability(self, odds: float) -> float:
        """Calculate implied probability from decimal odds."""
        if odds <= 1.0:
            return 1.0
        return 1.0 / odds
    
    def calculate_edge(self, model_prob: float, odds: float) -> float:
        """Calculate edge (model prob - implied prob)."""
        implied_prob = self.calculate_implied_probability(odds)
        return model_prob - implied_prob
    
    def kelly_stake(self, model_prob: float, odds: float) -> float:
        """Calculate Kelly stake."""
        if odds <= 1.0 or model_prob <= 0.0:
            return 0.0
        
        b = odds - 1
        p = model_prob
        q = 1 - model_prob
        
        kelly = (b * p - q) / b
        kelly *= self.kelly_fraction
        
        return max(0.0, min(kelly, 0.25))
    
    def run_backtest(
        self,
        predictions_df: pd.DataFrame,
        start_date: datetime,
        end_date: datetime,
        model_probability_col: str = 'model_probability',
        odds_col: str = 'odds',
        result_col: str = 'result'
    ) -> Dict[str, any]:
        """Run comprehensive backtest on historical predictions.
        
        Args:
            predictions_df: DataFrame with historical predictions
            start_date: Backtest start date
            end_date: Backtest end date
            model_probability_col: Column name for model probabilities
            odds_col: Column name for odds
            result_col: Column name for results (1 = win, 0 = loss)
            
        Returns:
            Dictionary of backtest results
        """
        # Filter by date range
        mask = (
            (predictions_df['date'] >= start_date) & 
            (predictions_df['date'] <= end_date)
        )
        filtered_df = predictions_df[mask].copy()
        
        if filtered_df.empty:
            return {'error': 'No predictions in date range'}
        
        # Initialize tracking variables
        bankroll = self.initial_bankroll
        total_staked = 0.0
        total_return = 0.0
        wins = 0
        losses = 0
        daily_pnl = []
        current_date = None
        daily_start_bankroll = 0.0
        
        # Track by confidence buckets
        confidence_buckets = {
            '70+': {'bets': 0, 'wins': 0, 'staked': 0.0, 'returned': 0.0},
            '60-69': {'bets': 0, 'wins': 0, 'staked': 0.0, 'returned': 0.0},
            '<60': {'bets': 0, 'wins': 0, 'staked': 0.0, 'returned': 0.0}
        }
        
        # Track by market type
        market_tracking = {}
        
        for _, row in filtered_df.iterrows():
            model_prob = row[model_probability_col]
            odds = row[odds_col]
            result = row[result_col]
            confidence = model_prob * 100
            market = row.get('market', 'Unknown')
            
            # Calculate stake
            stake_fraction = self.kelly_stake(model_prob, odds)
            stake_amount = stake_fraction * bankroll
            
            # Update tracking
            total_staked += stake_amount
            
            if result == 1:
                wins += 1
                return_amount = stake_amount * odds
                total_return += return_amount
                bankroll += (return_amount - stake_amount)
            else:
                losses += 1
                bankroll -= stake_amount
            
            # Track daily P&L
            row_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
            if current_date != row_date:
                if current_date is not None:
                    daily_pnl.append({
                        'date': current_date,
                        'pnl': bankroll - daily_start_bankroll,
                        'bankroll': bankroll
                    })
                current_date = row_date
                daily_start_bankroll = bankroll
            
            # Track confidence buckets
            if confidence >= 70:
                bucket = '70+'
            elif confidence >= 60:
                bucket = '60-69'
            else:
                bucket = '<60'
            
            confidence_buckets[bucket]['bets'] += 1
            confidence_buckets[bucket]['wins'] += result
            confidence_buckets[bucket]['staked'] += stake_amount
            if result == 1:
                confidence_buckets[bucket]['returned'] += stake_amount * odds
            
            # Track by market
            if market not in market_tracking:
                market_tracking[market] = {'bets': 0, 'wins': 0, 'staked': 0.0, 'returned': 0.0}
            market_tracking[market]['bets'] += 1
            market_tracking[market]['wins'] += result
            market_tracking[market]['staked'] += stake_amount
            if result == 1:
                market_tracking[market]['returned'] += stake_amount * odds
        
        # Calculate final metrics
        total_bets = wins + losses
        hit_rate = (wins / total_bets * 100) if total_bets > 0 else 0
        roi = ((total_return - total_staked) / total_staked * 100) if total_staked > 0 else 0
        yield_pct = ((bankroll - self.initial_bankroll) / self.initial_bankroll * 100)
        
        # Calculate Sharpe ratio (annualized)
        if daily_pnl:
            daily_returns = [day['pnl'] / self.initial_bankroll for day in daily_pnl]
            if len(daily_returns) > 1:
                avg_daily_return = np.mean(daily_returns)
                std_daily_return = np.std(daily_returns)
                sharpe_ratio = (avg_daily_return / std_daily_return) * np.sqrt(252) if std_daily_return > 0 else 0
            else:
                sharpe_ratio = 0
        else:
            sharpe_ratio = 0
        
        # Calculate maximum drawdown
        peak = self.initial_bankroll
        max_drawdown = 0
        for day in daily_pnl:
            if day['bankroll'] > peak:
                peak = day['bankroll']
            drawdown = (peak - day['bankroll']) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Statistical significance test
        if total_bets > 30:
            # Test if hit rate is significantly different from breakeven
            breakeven_hit_rate = 1 / (np.mean(filtered_df[odds_col]) if len(filtered_df) > 0 else 2.0)
            z_stat, p_value = stats.binomtest(wins, total_bets, breakeven_hit_rate).statistic, stats.binomtest(wins, total_bets, breakeven_hit_rate).pvalue
        else:
            z_stat, p_value = 0, 1.0
        
        # Compile results
        results = {
            'total_bets': total_bets,
            'wins': wins,
            'losses': losses,
            'hit_rate': round(hit_rate, 2),
            'total_staked': round(total_staked, 2),
            'total_returned': round(total_return, 2),
            'profit': round(bankroll - self.initial_bankroll, 2),
            'roi': round(roi, 2),
            'yield': round(yield_pct, 2),
            'final_bankroll': round(bankroll, 2),
            'sharpe_ratio': round(sharpe_ratio, 2),
            'max_drawdown': round(max_drawdown * 100, 2),
            'statistical_significance': {
                'z_statistic': round(z_stat, 3),
                'p_value': round(p_value, 4),
                'significant_at_5pct': p_value < 0.05
            },
            'confidence_buckets': {},
            'market_performance': {},
            'daily_pnl': daily_pnl[-10:] if daily_pnl else []  # Last 10 days
        }
        
        # Add confidence bucket results
        for bucket, data in confidence_buckets.items():
            if data['bets'] > 0:
                bucket_roi = ((data['returned'] - data['staked']) / data['staked'] * 100) if data['staked'] > 0 else 0
                results['confidence_buckets'][bucket] = {
                    'bets': data['bets'],
                    'wins': data['wins'],
                    'hit_rate': round(data['wins'] / data['bets'] * 100, 2),
                    'roi': round(bucket_roi, 2),
                    'profit': round(data['returned'] - data['staked'], 2)
                }
        
        # Add market performance
        for market, data in market_tracking.items():
            if data['bets'] > 0:
                market_roi = ((data['returned'] - data['staked']) / data['staked'] * 100) if data['staked'] > 0 else 0
                results['market_performance'][market] = {
                    'bets': data['bets'],
                    'wins': data['wins'],
                    'hit_rate': round(data['wins'] / data['bets'] * 100, 2),
                    'roi': round(market_roi, 2),
                    'profit': round(data['returned'] - data['staked'], 2)
                }
        
        return results
    
    def walk_forward_validation(
        self,
        predictions_df: pd.DataFrame,
        train_window_months: int = 6,
        test_window_months: int = 1,
        steps: int = 6
    ) -> Dict[str, any]:
        """Perform walk-forward validation.
        
        Args:
            predictions_df: DataFrame with historical predictions
            train_window_months: Training window in months
            test_window_months: Testing window in months
            steps: Number of walk-forward steps
            
        Returns:
            Dictionary of walk-forward validation results
        """
        results = []
        
        # Get date range
        min_date = predictions_df['date'].min()
        max_date = predictions_df['date'].max()
        
        for step in range(steps):
            # Calculate train and test periods
            test_end = max_date - timedelta(days=30 * (steps - step - 1))
            test_start = test_end - timedelta(days=30 * test_window_months)
            train_start = test_start - timedelta(days=30 * train_window_months)
            
            if train_start < min_date:
                continue
            
            # Split data
            train_data = predictions_df[
                (predictions_df['date'] >= train_start) & 
                (predictions_df['date'] < test_start)
            ]
            test_data = predictions_df[
                (predictions_df['date'] >= test_start) & 
                (predictions_df['date'] <= test_end)
            ]
            
            if test_data.empty or train_data.empty:
                continue
            
            # Train model (in real implementation, this would retrain the model)
            # For now, we'll just use the existing model probabilities
            
            # Run backtest on test period
            backtest_result = self.run_backtest(
                test_data,
                test_start,
                test_end
            )
            
            backtest_result['step'] = step + 1
            backtest_result['train_period'] = f"{train_start.date()} to {test_start.date()}"
            backtest_result['test_period'] = f"{test_start.date()} to {test_end.date()}"
            
            results.append(backtest_result)
        
        # Calculate average metrics across folds
        if results:
            avg_metrics = {
                'avg_roi': np.mean([r['roi'] for r in results]),
                'avg_yield': np.mean([r['yield'] for r in results]),
                'avg_hit_rate': np.mean([r['hit_rate'] for r in results]),
                'avg_sharpe': np.mean([r['sharpe_ratio'] for r in results]),
                'avg_max_drawdown': np.mean([r['max_drawdown'] for r in results]),
                'std_roi': np.std([r['roi'] for r in results]),
                'consistency_score': 1 - (np.std([r['roi'] for r in results]) / np.mean([r['roi'] for r in results])) if np.mean([r['roi'] for r in results]) != 0 else 0
            }
        else:
            avg_metrics = {}
        
        return {
            'walk_forward_results': results,
            'average_metrics': avg_metrics,
            'steps_completed': len(results)
        }
    
    def analyze_market_efficiency(
        self,
        predictions_df: pd.DataFrame
    ) -> Dict[str, any]:
        """Analyze market efficiency and identify value opportunities.
        
        Args:
            predictions_df: DataFrame with predictions including odds
            
        Returns:
            Dictionary of market efficiency analysis
        """
        # Calculate implied probabilities from odds
        predictions_df['implied_prob'] = predictions_df['odds'].apply(self.calculate_implied_probability)
        
        # Calculate edges
        predictions_df['edge'] = predictions_df['model_probability'] - predictions_df['implied_prob']
        
        # Identify value bets (positive edge)
        value_bets = predictions_df[predictions_df['edge'] > 0.02]  # 2% minimum edge
        
        # Analyze value bet performance
        if not value_bets.empty:
            value_results = self.run_backtest(
                value_bets,
                value_bets['date'].min(),
                value_bets['date'].max()
            )
        else:
            value_results = {'error': 'No value bets found'}
        
        # Calculate closing line value (if we have opening and closing odds)
        if 'opening_odds' in predictions_df.columns and 'closing_odds' in predictions_df.columns:
            predictions_df['opening_implied'] = predictions_df['opening_odds'].apply(self.calculate_implied_probability)
            predictions_df['closing_implied'] = predictions_df['closing_odds'].apply(self.calculate_implied_probability)
            predictions_df['clv'] = (predictions_df['closing_implied'] - predictions_df['opening_implied']) / predictions_df['opening_implied'] * 100
            
            avg_clv = predictions_df['clv'].mean()
        else:
            avg_clv = None
        
        return {
            'total_predictions': len(predictions_df),
            'value_bets_count': len(value_bets),
            'value_bets_percentage': round(len(value_bets) / len(predictions_df) * 100, 2) if len(predictions_df) > 0 else 0,
            'avg_edge': round(predictions_df['edge'].mean(), 4),
            'max_edge': round(predictions_df['edge'].max(), 4),
            'value_bet_performance': value_results,
            'avg_closing_line_value': round(avg_clv, 2) if avg_clv is not None else None,
            'market_efficiency_note': 'Lower value bet percentage and edge indicate more efficient markets'
        }