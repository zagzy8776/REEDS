from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.loaders import upsert_fixture
from app.services.data_quality import resolve_team_name


SHOWCASE_FIXTURES = {
    "soccer": [
        ("Club Friendlies", "Manchester City", "Barcelona"),
        ("International Friendly", "Germany", "Netherlands"),
    ],
    "basketball": [
        ("WNBA", "Las Vegas Aces", "Seattle Storm"),
        ("WNBA", "Chicago Sky", "Atlanta Dream"),
        ("NBA Summer League", "Los Angeles Lakers", "Boston Celtics"),
    ],
    "cricket": [
        ("T20 Blast", "Durham Jets", "Lancashire Lightning"),
        ("T20 Blast", "Essex Eagles", "Kent Spitfires"),
        ("International Cricket", "India", "South Africa"),
    ],
    "tennis": [
        ("ATP Tour", "ATP Player A", "ATP Player B"),
        ("WTA Tour", "WTA Player A", "WTA Player B"),
    ],
    "american_football": [
        ("NFL", "Kansas City Chiefs", "Buffalo Bills"),
        ("NFL", "Dallas Cowboys", "Philadelphia Eagles"),
    ],
    "baseball": [
        ("MLB", "New York Yankees", "Boston Red Sox"),
        ("MLB", "Los Angeles Dodgers", "San Francisco Giants"),
    ],
    "hockey": [
        ("NHL", "Toronto Maple Leafs", "Montreal Canadiens"),
        ("NHL", "New York Rangers", "Boston Bruins"),
    ],
    "rugby": [
        ("Rugby Union", "England", "Ireland"),
        ("Rugby Championship", "South Africa", "New Zealand"),
    ],
    "volleyball": [
        ("Volleyball Nations League", "Brazil", "Italy"),
        ("Volleyball Nations League", "Poland", "France"),
    ],
    "handball": [
        ("EHF Champions League", "Barcelona Handball", "PSG Handball"),
        ("EHF Champions League", "Kiel", "Veszprem"),
    ],
    "mma": [
        ("MMA Showcase", "Featherweight Fighter A", "Featherweight Fighter B"),
        ("MMA Showcase", "Welterweight Fighter A", "Welterweight Fighter B"),
    ],
}


def ensure_multisport_showcase(db: Session, min_upcoming_per_sport: int = 2) -> dict:
    """Keep the product visually multi-sport without spending API calls.

    This only tops up sports that have fewer than `min_upcoming_per_sport`
    upcoming fixtures. Real API rows always win through the normal upsert key;
    these rows are clearly marked as `coverage_seed` in the source/extra fields.
    It is intentionally API-free so the public board stays multi-sport even when
    free providers are rate limited, missing a sport, or temporarily down.
    """

    inserted = {}
    today = date.today()
    for sport, fixtures in SHOWCASE_FIXTURES.items():
        existing = db.query(Fixture).filter(Fixture.sport == sport, Fixture.match_date >= today).count()
        needed = max(0, min_upcoming_per_sport - existing)
        inserted[sport] = 0
        for idx, (league, home, away) in enumerate(fixtures[:needed]):
            match_date = today + timedelta(days=idx + (0 if sport in {"soccer", "basketball", "cricket", "tennis"} else 1))
            fx = Fixture(
                sport=sport,
                league=league,
                season=str(today.year),
                match_date=match_date,
                home_team=resolve_team_name(db, home, sport, "coverage_seed"),
                away_team=resolve_team_name(db, away, sport, "coverage_seed"),
                home_score=None,
                away_score=None,
                source="coverage_seed",
                extra={
                    "note": "Free-tier coverage seed. Replace with live API data when available.",
                    "status": "coverage_seed",
                    "is_showcase": True,
                },
            )
            upsert_fixture(db, fx)
            inserted[sport] += 1
    db.commit()
    return inserted