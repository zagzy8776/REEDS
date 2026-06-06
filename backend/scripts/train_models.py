import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.ml.train import train_basketball_model, train_soccer_model
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions


init_db()
db = SessionLocal()
try:
    data = dataframe_from_db(db)
    trained, skipped = [], []
    for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
        try:
            result = trainer(data)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            trained.append({"sport": sport, **result, "active": mv.is_active})
        except ValueError as exc:
            skipped.append({"sport": sport, "reason": str(exc)})
    generated = generate_today_predictions(db)
    print({"trained": trained, "skipped": skipped, "generated_predictions": generated})
finally:
    db.close()
