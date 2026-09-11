"""Admin endpoint: quarantine + delete invalid fixtures."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Fixture, Prediction
from app.db.rejected_fixture import RejectedFixture
from app.db.session import Base, engine, get_db
from app.services.fixture_quality import BAD_WORDS, save_rejected_fixture, validate_fixture

router = APIRouter()


def _require_admin(x_admin_key: str = Header(default="")):
    settings = get_settings()
    if settings.app_env == "production" and settings.admin_api_key in {"", "change-me"}:
        raise HTTPException(status_code=500, detail="Admin API key is not safely configured")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")


@router.post("/cleanup-invalid-fixtures", dependencies=[Depends(_require_admin)])
def cleanup_invalid_fixtures_endpoint(db: Session = Depends(get_db)):
    """Move garbage team-name fixtures into rejected_fixtures and delete them."""
    Base.metadata.create_all(bind=engine, tables=[RejectedFixture.__table__])
    moved = 0
    scanned = 0
    preds_removed = 0
    fixtures = db.query(Fixture).order_by(Fixture.id.asc()).all()
    for fx in fixtures:
        scanned += 1
        quality = validate_fixture(fx.home_team, fx.away_team, fx.sport)
        if quality.get("valid"):
            continue
        reason = str(quality.get("reason") or "invalid_team_name")
        save_rejected_fixture(
            db,
            provider=fx.source or "cleanup",
            raw_home=fx.home_team,
            raw_away=fx.away_team,
            reason=f"cleanup:{reason}",
            sport=fx.sport,
            league=fx.league,
            match_date=fx.match_date,
            raw_payload={"fixture_id": fx.id, "extra": fx.extra},
        )
        deleted = (
            db.query(Prediction)
            .filter(Prediction.fixture_id == fx.id)
            .delete(synchronize_session=False)
        )
        preds_removed += int(deleted or 0)
        db.delete(fx)
        moved += 1

    for word in BAD_WORDS:
        pattern = f"%{word}%"
        rows = (
            db.query(Fixture)
            .filter(or_(Fixture.home_team.ilike(pattern), Fixture.away_team.ilike(pattern)))
            .all()
        )
        for fx in rows:
            save_rejected_fixture(
                db,
                provider=fx.source or "cleanup",
                raw_home=fx.home_team,
                raw_away=fx.away_team,
                reason=f"cleanup:bad_word:{word}",
                sport=fx.sport,
                league=fx.league,
                match_date=fx.match_date,
                raw_payload={"fixture_id": fx.id},
            )
            deleted = (
                db.query(Prediction)
                .filter(Prediction.fixture_id == fx.id)
                .delete(synchronize_session=False)
            )
            preds_removed += int(deleted or 0)
            db.delete(fx)
            moved += 1

    db.commit()
    return {
        "ok": True,
        "scanned": scanned,
        "moved_to_quarantine": moved,
        "predictions_removed": preds_removed,
        "next": "Load historical CSVs, then POST /api/admin/train",
    }
