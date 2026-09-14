"""Soft fixture identity matching across providers."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.db.models import Fixture


def team_match_key(name: str) -> str:
    raw = str(name or "")
    for a, b in (
        ("æ", "ae"), ("ø", "o"), ("å", "a"),
        ("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"),
        ("Æ", "ae"), ("Ø", "o"), ("Å", "a"),
        ("é", "e"), ("è", "e"), ("ê", "e"), ("á", "a"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ñ", "n"), ("ç", "c"),
    ):
        raw = raw.replace(a, b)
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(c for c in raw if unicodedata.category(c) != "Mn")
    key = re.sub(r"[^a-z0-9 ]+", " ", raw.lower())
    key = re.sub(
        r"\b(fc|afc|cf|sc|ac|fk|nk|sk|bk|if|ik|sv|as|ssc|calcio|club|united|city|town)\b",
        " ",
        key,
    )
    return re.sub(r"\s+", " ", key).strip()


def teams_soft_equal(a: str, b: str) -> bool:
    ka, kb = team_match_key(a), team_match_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ca, cb = ka.replace(" ", ""), kb.replace(" ", "")
    if ca == cb:
        return True
    if len(ca) >= 4 and len(cb) >= 4:
        if ca in cb or cb in ca:
            return True
        if SequenceMatcher(None, ca, cb).ratio() >= 0.82:
            return True
        ta, tb = set(ka.split()), set(kb.split())
        if ta and tb and (ta <= tb or tb <= ta):
            return True
        inter = ta & tb
        if inter and len(inter) >= max(1, min(len(ta), len(tb)) - 1):
            return True
    return False


def find_fixture_soft(
    db: Session,
    *,
    sport: str,
    match_date,
    home_team: str,
    away_team: str,
    league: str | None = None,
) -> Fixture | None:
    """Find board fixture: exact league+teams, then teams only, then soft teams."""
    q = db.query(Fixture).filter(Fixture.sport == sport, Fixture.match_date == match_date)
    if league:
        row = (
            q.filter(
                Fixture.league == league,
                Fixture.home_team == home_team,
                Fixture.away_team == away_team,
            ).first()
        )
        if row:
            return row
    row = q.filter(Fixture.home_team == home_team, Fixture.away_team == away_team).first()
    if row:
        return row
    for row in q.limit(400).all():
        if teams_soft_equal(row.home_team, home_team) and teams_soft_equal(row.away_team, away_team):
            return row
    return None
