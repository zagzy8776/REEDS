import os
os.environ['DATABASE_URL'] = 'postgresql://neondb_owner:npg_CUh4KWHrFxT0@ep-lively-tooth-a2nottdc-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

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