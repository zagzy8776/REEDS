import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.ml.train import train_soccer_model
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions


init_db()
db = SessionLocal()
try:
    result = train_soccer_model(dataframe_from_db(db))
    mv = register_model(db, "soccer", result["model_type"], result["path"], result["accuracy"], result["sample_size"])
    generated = generate_today_predictions(db)
    print({"trained": result, "active": mv.is_active, "generated_predictions": generated})
finally:
    db.close()
