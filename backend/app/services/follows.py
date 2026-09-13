"""Team / league / sport follow infrastructure.

Anonymous users stay fully public; follows only influence personalized feed
ordering for a self-declared username. No auth wall is introduced.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import UserFollow

log = logging.getLogger(__name__)
VALID_TYPES = {"team", "league", "sport"}


def list_follows(db: Session, username: str) -> list[dict]:
    rows = (
        db.query(UserFollow)
        .filter(UserFollow.username == username.strip())
        .order_by(UserFollow.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {"id": row.id, "entity_type": row.entity_type, "entity_value": row.entity_value, "created_at": row.created_at.isoformat() if row.created_at else None}
        for row in rows
    ]


def toggle_follow(db: Session, username: str, entity_type: str, entity_value: str) -> dict:
    username = (username or "").strip()[:80]
    entity_value = (entity_value or "").strip()[:120]
    entity_type = (entity_type or "").strip().lower()[:20]
    if not username or not entity_value or entity_type not in VALID_TYPES:
        return {"ok": False, "followed": False, "error": "username, valid entity_type, and entity_value are required"}
    existing = (
        db.query(UserFollow)
        .filter_by(username=username, entity_type=entity_type, entity_value=entity_value)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()
        return {"ok": True, "followed": False, "entity_type": entity_type, "entity_value": entity_value}
    db.add(UserFollow(username=username, entity_type=entity_type, entity_value=entity_value, created_at=datetime.utcnow()))
    db.commit()
    return {"ok": True, "followed": True, "entity_type": entity_type, "entity_value": entity_value}