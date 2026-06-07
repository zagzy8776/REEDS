import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db
from app.ml.calibration import fit_soccer_platt_calibrator
from app.services.predictions import dataframe_from_db


if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    try:
        data = dataframe_from_db(db)
        result = fit_soccer_platt_calibrator(data[data.get("sport", "soccer") == "soccer"].copy())
        print({"calibrated": result})
    finally:
        db.close()