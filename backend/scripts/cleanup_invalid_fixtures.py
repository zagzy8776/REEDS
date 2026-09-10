#!/usr/bin/env python3
"""Move garbage fixture rows into rejected_fixtures quarantine.

Run once after deploying the fixture quality gate:

    cd backend && python -m scripts.cleanup_invalid_fixtures
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_

from app.db.models import Fixture, Prediction
from app.db.rejected_fixture import RejectedFixture
from app.db.session import SessionLocal, engine, Base
from app.services.fixture_quality import BAD_WORDS, validate_fixture, save_rejected_fixture


def main() -> int:
    Base.metadata.create_all(bind=engine, tables=[RejectedFixture.__table__])

    db = SessionLocal()
    moved = 0
    scanned = 0
    prediction_rows_removed = 0
    try:
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
            deleted = db.query(Prediction).filter(Prediction.fixture_id == fx.id).delete(synchronize_session=False)
            prediction_rows_removed += int(deleted or 0)
            db.delete(fx)
            moved += 1

        for word in BAD_WORDS:
            pattern = f"%{word}%"
            rows = db.query(Fixture).filter(or_(Fixture.home_team.ilike(pattern), Fixture.away_team.ilike(pattern))).all()
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
                deleted = db.query(Prediction).filter(Prediction.fixture_id == fx.id).delete(synchronize_session=False)
                prediction_rows_removed += int(deleted or 0)
                db.delete(fx)
                moved += 1

        long_rows = [fx for fx in db.query(Fixture).all() if len(fx.home_team or "") > 60 or len(fx.away_team or "") > 60]
        for fx in long_rows:
            save_rejected_fixture(
                db,
                provider=fx.source or "cleanup",
                raw_home=fx.home_team,
                raw_away=fx.away_team,
                reason="cleanup:name_too_long",
                sport=fx.sport,
                league=fx.league,
                match_date=fx.match_date,
                raw_payload={"fixture_id": fx.id},
            )
            deleted = db.query(Prediction).filter(Prediction.fixture_id == fx.id).delete(synchronize_session=False)
            prediction_rows_removed += int(deleted or 0)
            db.delete(fx)
            moved += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"cleanup_invalid_fixtures: scanned={scanned} moved={moved} predictions_removed={prediction_rows_removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
