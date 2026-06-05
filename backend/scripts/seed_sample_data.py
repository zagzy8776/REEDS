import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.models import Fixture
from app.db.session import SessionLocal, init_db


init_db()
db = SessionLocal()
base_played = [
    ("EPL", "2024", -30, "Arsenal", "Chelsea", 2, 1),
    ("EPL", "2024", -25, "Liverpool", "Tottenham", 3, 2),
    ("EPL", "2024", -20, "Chelsea", "Liverpool", 1, 1),
    ("EPL", "2024", -18, "Arsenal", "Tottenham", 2, 2),
    ("La Liga", "2024", -16, "Barcelona", "Real Madrid", 1, 2),
    ("La Liga", "2024", -13, "Atletico Madrid", "Barcelona", 1, 1),
    ("Serie A", "2024", -11, "Inter", "Milan", 2, 0),
    ("Serie A", "2024", -9, "Juventus", "Inter", 1, 1),
    ("EPL", "2024", -8, "Arsenal", "Liverpool", 2, 0),
    ("EPL", "2024", -7, "Chelsea", "Tottenham", 2, 1),
    ("La Liga", "2024", -6, "Real Madrid", "Atletico Madrid", 2, 1),
    ("Serie A", "2024", -5, "Milan", "Juventus", 1, 0),
]
played = []
for cycle in range(3):
    for league, season, offset, h, a, hs, aways in base_played:
        played.append((league, season, offset - (cycle * 40), h, a, hs, aways))
future = [
    ("EPL", "2024", 1, "Arsenal", "Liverpool"),
    ("EPL", "2024", 1, "Chelsea", "Tottenham"),
    ("La Liga", "2024", 2, "Barcelona", "Atletico Madrid"),
    ("Serie A", "2024", 2, "Inter", "Juventus"),
]
for league, season, offset, h, a, hs, aways in played:
    db.merge(Fixture(sport="soccer", league=league, season=season, match_date=date.today() + timedelta(days=offset), home_team=h, away_team=a, home_score=hs, away_score=aways, home_odds=2.0, draw_odds=3.2, away_odds=3.5, source="sample"))
for league, season, offset, h, a in future:
    db.merge(Fixture(sport="soccer", league=league, season=season, match_date=date.today() + timedelta(days=offset), home_team=h, away_team=a, source="sample"))
db.commit()
db.close()
print("Seeded sample fixtures")
