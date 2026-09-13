"""Post-ingestion fixture normalization for provider sport metadata."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Fixture

log = logging.getLogger(__name__)

HINTS = (
    ("fiba", "basketball"),
    ("wnba", "basketball"),
    ("nba", "basketball"),
    ("euroleague", "basketball"),
    ("ncaa basketball", "basketball"),
    ("mlb", "baseball"),
    ("npb", "baseball"),
    ("khl", "hockey"),
    ("nhl", "hockey"),
    ("iihf", "hockey"),
    ("cfl", "american_football"),
    ("nfl", "american_football"),
    ("ncaa football", "american_football"),
    ("rugby", "rugby"),
    ("six nations", "rugby"),
    ("super rugby", "rugby"),
    ("atp", "tennis"),
    ("wta", "tennis"),
    ("wimbledon", "tennis"),
    ("roland garros", "tennis"),
    ("t20", "cricket"),
    ("test match", "cricket"),
    ("ipl", "cricket"),
)


def _expected_sport(league: str, current: str) -> str:
    low = (league or "").lower()
    for fragment, sport in HINTS:
        if fragment in low:
            return sport
    return current


def normalize_fixture_sports(db: Session) -> dict:
    """Correct provider sport mistakes where the competition name is decisive.

    When a correctly typed counterpart already exists, the bad row is retired and
    its predictions are moved to the correctly typed fixture before deletion.
    """

    from app.db.models import Prediction

    scanned = 0
    corrected = 0
    merged = 0
    for fixture in db.query(Fixture).all():
        scanned += 1
        target = _expected_sport(fixture.league, fixture.sport)
        if target == fixture.sport:
            continue

        counterpart = (
            db.query(Fixture)
            .filter(
                Fixture.id != fixture.id,
                Fixture.sport == target,
                Fixture.match_date == fixture.match_date,
                Fixture.home_team == fixture.home_team,
                Fixture.away_team == fixture.away_team,
            )
            .first()
        )
        if counterpart:
            db.query(Prediction).filter(Prediction.fixture_id == fixture.id).update(
                {Prediction.fixture_id: counterpart.id}, synchronize_session=False
            )
            db.delete(fixture)
            merged += 1
        else:
            fixture.sport = target
            corrected += 1

    db.commit()
    result = {"scanned": scanned, "corrected": corrected, "merged": merged}
    log.info("Fixture sport normalization: %s", result)
    return result
