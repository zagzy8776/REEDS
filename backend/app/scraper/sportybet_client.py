"""SportyBet scraper client for extracting odds and fixtures.

This module provides functionality to scrape SportyBet for:
- Match fixtures and odds
- Multiple betting markets (1X2, Over/Under, BTTS, etc.)
- Live odds updates
"""

import logging
import random
import time
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class SportyBetClient:
    """Client for scraping SportyBet odds and fixtures with rate limiting."""
    
    def __init__(
        self,
        base_url: str = "https://www.sportybet.com",
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        max_retries: int = 3
    ):
        """Initialize SportyBet client with rate limiting.
        
        Args:
            base_url: Base URL for SportyBet website
            min_delay: Minimum delay between requests in seconds
            max_delay: Maximum delay between requests in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        })
        self._last_request_time = 0
    
    def _random_delay(self):
        """Add random delay between requests to avoid detection."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_delay:
            delay = random.uniform(self.min_delay, self.max_delay)
            time.sleep(delay)
        self._last_request_time = time.time()
    
    def _make_request(self, url: str, params: dict = None) -> Optional[requests.Response]:
        """Make HTTP request with retries and error handling.
        
        Args:
            url: URL to request
            params: Optional query parameters
            
        Returns:
            Response object or None if all retries failed
        """
        for attempt in range(self.max_retries):
            try:
                self._random_delay()
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    # Exponential backoff
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait_time)
                else:
                    logger.error(f"All {self.max_retries} attempts failed for {url}")
        return None
    
    def get_soccer_fixtures(self, country: str = "NG", date_filter: Optional[date] = None) -> List[Dict]:
        """Get soccer fixtures from SportyBet.
        
        Args:
            country: Country code (NG for Nigeria, etc.)
            date_filter: Optional date to filter fixtures
            
        Returns:
            List of fixture dictionaries with odds
        """
        fixtures = []
        
        try:
            # Construct URL for soccer betting
            url = f"{self.base_url}/ng/sport/football"
            if date_filter:
                url += f"/{date_filter.strftime('%Y-%m-%d')}"
            
            response = self._make_request(url)
            if not response:
                logger.warning("Failed to fetch SportyBet fixtures")
                return fixtures
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find fixture containers - this selector may need adjustment based on actual HTML structure
            fixture_elements = soup.find_all('div', {'class': 'fixture-item'}) or \
                             soup.find_all('div', {'data-fixture-id': True})
            
            for element in fixture_elements:
                try:
                    fixture_data = self._parse_fixture_element(element)
                    if fixture_data and (not date_filter or fixture_data.get('match_date') == date_filter):
                        fixtures.append(fixture_data)
                except Exception as e:
                    logger.warning(f"Error parsing fixture element: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error fetching SportyBet fixtures: {e}")
        
        return fixtures
    
    def _parse_fixture_element(self, element) -> Optional[Dict]:
        """Parse a single fixture element from HTML.
        
        Args:
            element: BeautifulSoup element representing a fixture
            
        Returns:
            Dictionary with fixture data
        """
        try:
            # Extract teams
            home_team = element.find('span', {'class': 'home-team'}) or \
                       element.find('div', {'class': 'team-home'})
            away_team = element.find('span', {'class': 'away-team'}) or \
                       element.find('div', {'class': 'team-away'})
            
            if not home_team or not away_team:
                return None
            
            home_team_name = home_team.get_text(strip=True)
            away_team_name = away_team.get_text(strip=True)
            
            # Extract match date and time
            match_datetime = element.find('div', {'class': 'match-time'}) or \
                           element.find('span', {'class': 'time'})
            
            match_date = None
            match_time = None
            if match_datetime:
                datetime_text = match_datetime.get_text(strip=True)
                # Parse datetime - format may vary
                try:
                    match_datetime_obj = datetime.strptime(datetime_text, '%Y-%m-%d %H:%M')
                    match_date = match_datetime_obj.date()
                    match_time = match_datetime_obj.time()
                except ValueError:
                    # Try alternative formats
                    for fmt in ['%d/%m/%Y %H:%M', '%m/%d/%Y %H:%M', '%Y-%m-%d']:
                        try:
                            match_datetime_obj = datetime.strptime(datetime_text, fmt)
                            match_date = match_datetime_obj.date()
                            match_time = match_datetime_obj.time()
                            break
                        except ValueError:
                            continue
            
            # Extract league
            league_element = element.find('div', {'class': 'league-name'}) or \
                           element.find('span', {'class': 'competition'})
            league_name = league_element.get_text(strip=True) if league_element else "Unknown"
            
            # Extract odds
            odds = self._parse_odds(element)
            
            return {
                'home_team': home_team_name,
                'away_team': away_team_name,
                'match_date': match_date,
                'match_time': match_time,
                'league': league_name,
                'sport': 'soccer',
                'odds': odds,
                'source': 'sportybet'
            }
            
        except Exception as e:
            logger.warning(f"Error parsing fixture element: {e}")
            return None
    
    def _parse_odds(self, element) -> Dict[str, float]:
        """Parse odds from a fixture element.
        
        Args:
            element: BeautifulSoup element representing a fixture
            
        Returns:
            Dictionary mapping market types to odds
        """
        odds = {}
        
        try:
            # Look for odds buttons/elements
            odds_elements = element.find_all('button', {'class': 'odds-button'}) or \
                          element.find_all('div', {'class': 'odds-item'})
            
            for odds_elem in odds_elements:
                market_name = odds_elem.get('data-market', '').strip()
                odds_value = odds_elem.get_text(strip=True)
                
                # Clean and parse odds value
                try:
                    odds_float = float(odds_value)
                    
                    # Map market names to standard format
                    market_mapping = {
                        '1': 'home_win',
                        'X': 'draw',
                        '2': 'away_win',
                        '1X': 'home_or_draw',
                        '12': 'home_or_away',
                        'X2': 'away_or_draw',
                        'Over': 'over',
                        'Under': 'under',
                        'Yes': 'btts_yes',
                        'No': 'btts_no'
                    }
                    
                    # Handle different market types
                    if '1X2' in market_name or 'Match Winner' in market_name:
                        if '1' in market_name:
                            odds['home_win'] = odds_float
                        elif 'X' in market_name:
                            odds['draw'] = odds_float
                        elif '2' in market_name:
                            odds['away_win'] = odds_float
                    
                    elif 'Double Chance' in market_name:
                        if '1X' in market_name:
                            odds['home_or_draw'] = odds_float
                        elif '12' in market_name:
                            odds['home_or_away'] = odds_float
                        elif 'X2' in market_name:
                            odds['away_or_draw'] = odds_float
                    
                    elif 'Over/Under' in market_name or 'Goals' in market_name:
                        if 'Over' in market_name:
                            odds['over_2.5'] = odds_float  # Default to 2.5, could be extracted
                        elif 'Under' in market_name:
                            odds['under_2.5'] = odds_float
                    
                    elif 'Both Teams to Score' in market_name or 'BTTS' in market_name:
                        if 'Yes' in market_name:
                            odds['btts_yes'] = odds_float
                        elif 'No' in market_name:
                            odds['btts_no'] = odds_float
                    
                except ValueError:
                    continue
                    
        except Exception as e:
            logger.warning(f"Error parsing odds: {e}")
        
        return odds
    
    def get_live_fixtures(self) -> List[Dict]:
        """Get live/in-play fixtures from SportyBet.
        
        Returns:
            List of live fixture dictionaries
        """
        live_fixtures = []
        
        try:
            url = f"{self.base_url}/ng/sport/football/live"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find live fixture elements
            live_elements = soup.find_all('div', {'class': 'live-fixture'}) or \
                          soup.find_all('div', {'data-live': 'true'})
            
            for element in live_elements:
                fixture_data = self._parse_fixture_element(element)
                if fixture_data:
                    fixture_data['is_live'] = True
                    live_fixtures.append(fixture_data)
                    
        except Exception as e:
            logger.error(f"Error fetching live fixtures: {e}")
        
        return live_fixtures
    
    def get_odds_movement(self, fixture_id: str) -> Optional[Dict]:
        """Get odds movement history for a specific fixture.
        
        Args:
            fixture_id: SportyBet fixture ID
            
        Returns:
            Dictionary with odds movement data
        """
        try:
            url = f"{self.base_url}/api/odds-history/{fixture_id}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching odds movement: {e}")
            return None
    
    def normalize_odds(self, sportybet_odds: Dict[str, float]) -> Dict[str, Optional[float]]:
        """Normalize SportyBet odds to standard format.
        
        Args:
            sportybet_odds: Raw odds from SportyBet
            
        Returns:
            Normalized odds dictionary with standard keys
        """
        normalized = {
            'home_odds': sportybet_odds.get('home_win'),
            'draw_odds': sportybet_odds.get('draw'),
            'away_odds': sportybet_odds.get('away_win'),
            'over_2_5_odds': sportybet_odds.get('over_2.5'),
            'under_2_5_odds': sportybet_odds.get('under_2.5'),
            'btts_yes_odds': sportybet_odds.get('btts_yes'),
            'btts_no_odds': sportybet_odds.get('btts_no'),
            'home_or_draw_odds': sportybet_odds.get('home_or_draw'),
            'home_or_away_odds': sportybet_odds.get('home_or_away'),
            'away_or_draw_odds': sportybet_odds.get('away_or_draw'),
        }
        
        return normalized


def ingest_sportybet_fixtures(db, target_dates: List[str]) -> int:
    """Ingest fixtures from SportyBet into database.
    
    Args:
        db: Database session
        target_dates: List of date strings to ingest
        
    Returns:
        Number of fixtures ingested
    """
    from app.db.models import Fixture
    from app.scraper.loaders import upsert_fixture
    from app.services.data_quality import resolve_team_name
    
    client = SportyBetClient()
    count = 0
    
    for date_str in target_dates:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            fixtures = client.get_soccer_fixtures(date_filter=target_date)
            
            for fixture_data in fixtures:
                try:
                    home_team = resolve_team_name(db, fixture_data['home_team'], 'soccer', 'sportybet')
                    away_team = resolve_team_name(db, fixture_data['away_team'], 'soccer', 'sportybet')
                    
                    odds = client.normalize_odds(fixture_data.get('odds', {}))
                    
                    fx = Fixture(
                        sport='soccer',
                        league=fixture_data.get('league', 'Unknown'),
                        season=str(target_date.year),
                        match_date=fixture_data.get('match_date', target_date),
                        home_team=home_team,
                        away_team=away_team,
                        home_score=None,
                        away_score=None,
                        home_odds=odds['home_odds'],
                        draw_odds=odds['draw_odds'],
                        away_odds=odds['away_odds'],
                        source='sportybet',
                        extra={
                            'over_2_5_odds': odds['over_2_5_odds'],
                            'under_2_5_odds': odds['under_2_5_odds'],
                            'btts_yes_odds': odds['btts_yes_odds'],
                            'btts_no_odds': odds['btts_no_odds'],
                            'double_chance_odds': {
                                'home_or_draw': odds['home_or_draw_odds'],
                                'home_or_away': odds['home_or_away_odds'],
                                'away_or_draw': odds['away_or_draw_odds']
                            }
                        }
                    )
                    
                    upsert_fixture(db, fx)
                    count += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing SportyBet fixture: {e}")
                    continue
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Error ingesting SportyBet fixtures for {date_str}: {e}")
            db.rollback()
            continue
    
    return count