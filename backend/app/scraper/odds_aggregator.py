"""Multi-source odds aggregation system with fallback mechanisms.

This module provides robust odds collection from multiple sources to prevent
single points of failure and ensure data availability.
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class OddsAggregator:
    """Aggregates odds from multiple sources with intelligent fallback."""
    
    def __init__(self, db: Session):
        """Initialize odds aggregator.
        
        Args:
            db: Database session
        """
        self.db = db
        self.source_priority = [
            'sportybet',      # Primary (free, but may be unreliable)
            'api_football',   # Fallback 1 (paid, reliable)
            'the_odds_api',   # Fallback 2 (paid, comprehensive)
            'sportmonks',     # Fallback 3 (paid, good coverage)
            'allsportsapi',   # Fallback 4 (free tier available)
        ]
        
        # Track source reliability
        self.source_reliability = {
            'sportybet': 0.8,
            'api_football': 0.95,
            'the_odds_api': 0.9,
            'sportmonks': 0.85,
            'allsportsapi': 0.7,
        }
    
    def get_best_odds_for_fixture(
        self,
        home_team: str,
        away_team: str,
        match_date: date,
        league: str
    ) -> Optional[Dict[str, float]]:
        """Get best available odds for a specific fixture.
        
        Args:
            home_team: Home team name
            away_team: Away team name
            match_date: Match date
            league: League name
            
        Returns:
            Dictionary with best odds for each market, or None if unavailable
        """
        all_odds = {}
        
        # Try each source in priority order
        for source in self.source_priority:
            try:
                odds = self._get_odds_from_source(
                    source, home_team, away_team, match_date, league
                )
                if odds:
                    all_odds[source] = odds
                    # Update reliability
                    self.source_reliability[source] = min(
                        1.0, self.source_reliability[source] + 0.02
                    )
            except Exception as e:
                logger.warning(f"Source {source} failed: {e}")
                self.source_reliability[source] = max(
                    0.0, self.source_reliability[source] - 0.05
                )
        
        if not all_odds:
            return None
        
        # Select best odds for each market
        return self._select_best_odds(all_odds)
    
    def _get_odds_from_source(
        self,
        source: str,
        home_team: str,
        away_team: str,
        match_date: date,
        league: str
    ) -> Optional[Dict[str, float]]:
        """Get odds from a specific source.
        
        Args:
            source: Source identifier
            home_team: Home team name
            away_team: Away team name
            match_date: Match date
            league: League name
            
        Returns:
            Dictionary of odds or None
        """
        # This would integrate with actual API clients
        # For now, return None to indicate source unavailable
        
        # In production, this would call:
        # if source == 'sportybet':
        #     return self._get_sportybet_odds(home_team, away_team, match_date)
        # elif source == 'api_football':
        #     return self._get_api_football_odds(home_team, away_team, match_date)
        # etc.
        
        return None
    
    def _select_best_odds(
        self,
        odds_collection: Dict[str, Dict[str, float]]
    ) -> Dict[str, float]:
        """Select best odds from multiple sources.
        
        For each market, selects the highest odds (best for bettor).
        
        Args:
            odds_collection: Dictionary mapping source to odds
            
        Returns:
            Dictionary with best odds for each market
        """
        best_odds = {}
        
        # Define markets to check
        markets = [
            'home_win', 'draw', 'away_win',
            'over_2_5', 'under_2_5',
            'btts_yes', 'btts_no',
            'home_or_draw', 'away_or_draw', 'home_or_away'
        ]
        
        for market in markets:
            best_value = None
            best_source = None
            
            for source, odds in odds_collection.items():
                if market in odds and odds[market] is not None:
                    if best_value is None or odds[market] > best_value:
                        best_value = odds[market]
                        best_source = source
            
            if best_value is not None:
                best_odds[market] = best_value
                best_odds[f'{market}_source'] = best_source
        
        return best_odds
    
    def detect_line_movement(
        self,
        fixture_id: int,
        current_odds: Dict[str, float],
        threshold: float = 0.15
    ) -> Dict[str, any]:
        """Detect significant line movement.
        
        Compares current odds with historical odds to identify market moves.
        
        Args:
            fixture_id: Fixture ID
            current_odds: Current odds
            threshold: Movement threshold (15% = significant)
            
        Returns:
            Dictionary with movement analysis
        """
        from app.db.models import OddsSnapshot
        
        # Get historical odds for this fixture
        historical = self.db.query(OddsSnapshot).filter(
            OddsSnapshot.fixture_id == fixture_id
        ).order_by(OddsSnapshot.captured_at.desc()).all()
        
        if not historical:
            return {
                'movement_detected': False,
                'reason': 'No historical odds available'
            }
        
        # Get earliest odds (opening line)
        opening_odds = historical[-1]
        
        movements = {}
        significant_movement = False
        
        for market in ['home_odds', 'draw_odds', 'away_odds']:
            opening = getattr(opening_odds, market)
            current = current_odds.get(market.replace('_odds', '_win'))
            
            if opening and current:
                movement_pct = abs(current - opening) / opening
                movements[market] = {
                    'opening': opening,
                    'current': current,
                    'movement_pct': round(movement_pct * 100, 2),
                    'significant': movement_pct > threshold
                }
                
                if movement_pct > threshold:
                    significant_movement = True
        
        return {
            'movement_detected': significant_movement,
            'movements': movements,
            'threshold_pct': threshold * 100,
            'recommendation': 'AVOID' if significant_movement else 'PROCEED'
        }
    
    def validate_odds_quality(self, odds: Dict[str, float]) -> Dict[str, any]:
        """Validate odds quality and completeness.
        
        Args:
            odds: Odds dictionary
            
        Returns:
            Validation results
        """
        issues = []
        warnings = []
        
        # Check for required markets
        required = ['home_win', 'draw', 'away_win']
        for market in required:
            if market not in odds or odds[market] is None:
                issues.append(f'Missing required market: {market}')
        
        # Check for reasonable odds (not too low or too high)
        for market, value in odds.items():
            if value is not None and market != 'source':
                if value < 1.01:
                    warnings.append(f'{market} odds too low: {value}')
                elif value > 100:
                    warnings.append(f'{market} odds suspiciously high: {value}')
        
        # Check for arbitrage opportunities (might indicate error)
        if all(m in odds and odds[m] is not None for m in required):
            implied_probs = sum(1/odds[m] for m in required)
            if implied_probs < 0.9:  # Less than 90% = likely error
                issues.append(f'Implied probabilities sum to {implied_probs:.3f} (likely error)')
            elif implied_probs > 1.2:  # More than 120% = unusual
                warnings.append(f'High overround: {implied_probs:.3f}')
        
        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'warnings': warnings,
            'quality_score': max(0, 100 - len(issues) * 20 - len(warnings) * 5)
        }
    
    def get_odds_with_confidence(
        self,
        home_team: str,
        away_team: str,
        match_date: date,
        league: str
    ) -> Optional[Dict[str, any]]:
        """Get odds with confidence score.
        
        Combines odds aggregation with quality validation and movement detection.
        
        Args:
            home_team: Home team name
            away_team: Away team name
            match_date: Match date
            league: League name
            
        Returns:
            Dictionary with odds, confidence score, and metadata
        """
        # Get best available odds
        odds = self.get_best_odds_for_fixture(
            home_team, away_team, match_date, league
        )
        
        if not odds:
            return None
        
        # Validate quality
        quality = self.validate_odds_quality(odds)
        
        # Calculate confidence score
        confidence = quality['quality_score']
        
        # Adjust for source reliability
        sources_used = set()
        for key, value in odds.items():
            if key.endswith('_source'):
                sources_used.add(value)
        
        avg_reliability = sum(
            self.source_reliability.get(source, 0.5)
            for source in sources_used
        ) / max(len(sources_used), 1)
        
        confidence *= avg_reliability
        
        return {
            'odds': odds,
            'confidence': round(confidence, 2),
            'quality': quality,
            'sources_used': list(sources_used),
            'timestamp': datetime.utcnow().isoformat()
        }


def ingest_odds_for_fixtures(db: Session, target_dates: List[str]) -> int:
    """Ingest odds for fixtures on specific dates.
    
    Args:
        db: Database session
        target_dates: List of date strings
        
    Returns:
        Number of fixtures with odds updated
    """
    from app.db.models import Fixture, OddsSnapshot
    from datetime import datetime as dt
    
    aggregator = OddsAggregator(db)
    updated_count = 0
    
    for date_str in target_dates:
        try:
            target_date = dt.strptime(date_str, '%Y-%m-%d').date()
            
            # Get all fixtures for this date without odds
            fixtures = db.query(Fixture).filter(
                func.date(Fixture.match_date) == target_date,
                Fixture.home_odds == None
            ).all()
            
            for fixture in fixtures:
                try:
                    odds_result = aggregator.get_odds_with_confidence(
                        fixture.home_team,
                        fixture.away_team,
                        fixture.match_date,
                        fixture.league
                    )
                    
                    if odds_result and odds_result['confidence'] > 60:
                        odds = odds_result['odds']
                        
                        # Update fixture with best odds
                        fixture.home_odds = odds.get('home_win')
                        fixture.draw_odds = odds.get('draw')
                        fixture.away_odds = odds.get('away_win')
                        
                        # Store snapshot for tracking
                        snapshot = OddsSnapshot(
                            fixture_id=fixture.id,
                            phase='opening',
                            market='1X2',
                            home_odds=odds.get('home_win'),
                            draw_odds=odds.get('draw'),
                            away_odds=odds.get('away_win'),
                            source='aggregated',
                            bookmaker=','.join(odds_result['sources_used'])
                        )
                        db.add(snapshot)
                        
                        updated_count += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing fixture {fixture.id}: {e}")
                    continue
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Error ingesting odds for {date_str}: {e}")
            db.rollback()
            continue
    
    return updated_count