import logging
from dotenv import load_dotenv

load_dotenv("../.env", override=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
from app.db.session import init_db, engine
from sqlalchemy import inspect

print("START init_db against Aiven ...", flush=True)
init_db()
names = sorted(inspect(engine).get_table_names())
print("AIVEN init_db OK; public tables:", len(names), flush=True)
print(names, flush=True)
engine.dispose()
print("DONE", flush=True)