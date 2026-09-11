import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.services.data_quality import upsert_team_alias


ALIASES = [
    ("soccer", "Sporting CP", "Sp Lisbon"),
    ("soccer", "Sporting CP", "Sporting Lisbon"),
    ("soccer", "Sporting CP", "Sporting"),
    ("soccer", "Benfica", "SL Benfica"),
    ("soccer", "Porto", "FC Porto"),
    ("soccer", "Manchester United", "Man United"),
    ("soccer", "Manchester United", "Man Utd"),
    ("soccer", "Manchester City", "Man City"),
    ("soccer", "Tottenham Hotspur", "Tottenham"),
    ("soccer", "Tottenham Hotspur", "Spurs"),
    ("soccer", "Wolverhampton Wanderers", "Wolves"),
    ("soccer", "Brighton & Hove Albion", "Brighton"),
    ("soccer", "Newcastle United", "Newcastle"),
    ("soccer", "West Ham United", "West Ham"),
    ("soccer", "Inter", "Inter Milan"),
    ("soccer", "Inter", "Internazionale"),
    ("soccer", "Milan", "AC Milan"),
    ("soccer", "Juventus", "Juve"),
    ("soccer", "Atletico Madrid", "Ath Madrid"),
    ("soccer", "Barcelona", "FC Barcelona"),
    ("soccer", "Real Madrid", "Real"),
    ("soccer", "Bayern Munich", "Bayern München"),
    ("soccer", "Bayern Munich", "Bayern Munchen"),
    ("soccer", "Borussia Dortmund", "Dortmund"),
    ("soccer", "Paris Saint-Germain", "Paris SG"),
    ("soccer", "Paris Saint-Germain", "PSG"),
    ("soccer", "West Bromwich Albion", "West Brom"),
    ("soccer", "West Bromwich Albion", "WBA"),
    ("soccer", "Queens Park Rangers", "QPR"),
    ("soccer", "Nottingham Forest", "Nottm Forest"),
    ("soccer", "Nottingham Forest", "Forest"),
    ("soccer", "Sheffield United", "Sheff Utd"),
    ("soccer", "Sheffield Wednesday", "Sheff Wed"),
    ("soccer", "Brighton & Hove Albion", "Brighton and Hove Albion"),
    ("soccer", "AFC Bournemouth", "Bournemouth"),
    ("soccer", "Crystal Palace", "Palace"),
    ("soccer", "Bayer Leverkusen", "Leverkusen"),
    ("soccer", "RB Leipzig", "Leipzig"),
    ("soccer", "Athletic Club", "Athletic Bilbao"),
    ("soccer", "Athletic Club", "Ath Bilbao"),
    ("soccer", "Marseille", "Olympique Marseille"),
    ("soccer", "Marseille", "OM"),
    ("soccer", "Lyon", "Olympique Lyonnais"),
    ("soccer", "Lyon", "OL"),
    ("soccer", "Napoli", "SSC Napoli"),
    ("soccer", "Roma", "AS Roma"),
    ("soccer", "Ajax", "AFC Ajax"),
    ("soccer", "PSV Eindhoven", "PSV"),
    ("soccer", "Celtic", "Celtic FC"),
    ("soccer", "Rangers", "Rangers FC"),
    ("soccer", "Rangers", "Glasgow Rangers"),
    ("soccer", "Sunderland", "Sunderland AFC"),
    ("soccer", "Luton Town", "Luton"),
    ("soccer", "Burnley", "Burnley FC"),
    ("soccer", "Brentford", "Brentford FC"),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for sport, canonical, alias in ALIASES:
            print(upsert_team_alias(db, sport, canonical, alias, source="seed"), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
