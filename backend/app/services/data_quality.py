import re

from sqlalchemy.orm import Session

from app.db.models import Team, TeamAlias
from app.utils.team_names import normalize_team_name


def alias_key(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def resolve_team_name(db: Session, raw_name: str, sport: str, source: str = "ingestion") -> str:
    """Resolve raw team strings through DB aliases, then static aliases.

    Unknown names are promoted to canonical teams with a self-alias so future rows
    become stable. This gives us data quality today without blocking ingestion.
    """

    key = alias_key(raw_name)
    existing_alias = db.query(TeamAlias).filter(TeamAlias.sport == sport, TeamAlias.alias_key == key).first()
    if existing_alias:
        team = db.query(Team).filter(Team.id == existing_alias.team_id).first()
        if team:
            return team.canonical_name

    canonical = normalize_team_name(raw_name, sport)
    team = db.query(Team).filter(Team.sport == sport, Team.canonical_name == canonical).first()
    if not team:
        team = Team(sport=sport, canonical_name=canonical)
        db.add(team)
        db.flush()

    db.add(TeamAlias(team_id=team.id, sport=sport, alias=str(raw_name).strip(), alias_key=key, source=source))
    canonical_key = alias_key(canonical)
    if canonical_key != key and not db.query(TeamAlias).filter(TeamAlias.sport == sport, TeamAlias.alias_key == canonical_key).first():
        db.add(TeamAlias(team_id=team.id, sport=sport, alias=canonical, alias_key=canonical_key, source="canonical"))
    db.flush()
    return canonical


def upsert_team_alias(db: Session, sport: str, canonical_name: str, alias: str, source: str = "manual") -> dict:
    canonical = normalize_team_name(canonical_name, sport)
    team = db.query(Team).filter(Team.sport == sport, Team.canonical_name == canonical).first()
    if not team:
        team = Team(sport=sport, canonical_name=canonical)
        db.add(team)
        db.flush()
    key = alias_key(alias)
    existing = db.query(TeamAlias).filter(TeamAlias.sport == sport, TeamAlias.alias_key == key).first()
    if existing:
        existing.team_id = team.id
        existing.alias = alias
        existing.source = source
    else:
        db.add(TeamAlias(team_id=team.id, sport=sport, alias=alias, alias_key=key, source=source))
    db.commit()
    return {"sport": sport, "canonical_name": canonical, "alias": alias}