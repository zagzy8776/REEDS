"""Manual prediction-generation smoke script.

Requires a real DATABASE_URL in the environment (never committed):

    DATABASE_URL='postgresql://...' ADMIN_API_KEY='...' python test_prediction_gen.py

This script previously contained a hardcoded production connection string.
It must never hold credentials again — see docs/credential-rotation.md.
"""

import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    sys.exit(
        "DATABASE_URL is not set. Export a PostgreSQL URL for your target database "
        "before running this script (never hardcode it here)."
    )
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ.setdefault("APP_ENV", "production")

from app.db.session import SessionLocal
from app.services.predictions import generate_today_predictions

db = SessionLocal()
try:
    count = generate_today_predictions(db)
    print(f"generated {count}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
finally:
    db.close()
