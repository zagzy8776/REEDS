# LOYAL EDGE — Sports Prediction Website

LOYAL EDGE is a production-style sports analytics website for soccer and basketball expansion. Public users see **EDGE Score**, **Value Rating**, **Risk Level**, and 3-leg combos. Internally, the backend uses a hybrid analytics engine: form features + Poisson-style scoring model + XGBoost/RandomForest classifiers + risk filters.

> Predictions are probabilistic, not guarantees. Keep metrics transparent and do not market fake proof or fixed results.

## Stack

- Backend: Python 3.10+, FastAPI, SQLAlchemy, pandas, scikit-learn, XGBoost
- Frontend: Next.js + TypeScript + Tailwind CSS
- Database: Neon PostgreSQL via `DATABASE_URL`
- Deploy: Render backend + Vercel frontend

## Local setup

```bash
cp .env.example .env
# Put your rotated Neon DATABASE_URL in .env. Do not commit secrets.
pip install -r backend/requirements.txt
python backend/scripts/seed_sample_data.py
python backend/scripts/train_models.py
uvicorn app.main:app --app-dir backend --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Data sources

Supported through loaders/adapters:

- football-data.co.uk CSVs
- footballcsv.github.io CSVs
- Kaggle CSV exports copied into `data/raw/`
- GitHub NBA datasets copied into `data/raw/`
- FBref/SofaScore adapters can be extended where permitted by their terms
- API-Football-style adapter is included in `backend/app/scraper/api_clients.py`
- HTTP scraping helper supports user-agent rotation and optional proxies from env/config

Load football CSV:

```bash
python backend/scripts/load_csv_data.py --path data/raw/E0.csv --league EPL --season 2024
```

## Render backend

- Root Directory: `backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Runtime: Python 3.11.9. The file `backend/runtime.txt` pins this because Python 3.14 can force pandas/xgboost source builds.
- Env Vars: `PYTHON_VERSION=3.11.9`, `DATABASE_URL`, `ADMIN_API_KEY`, `APP_ENV=production`, `ENABLE_SCHEDULER=true`

## Vercel frontend

- Root Directory: `frontend`
- Env Var: `NEXT_PUBLIC_API_URL=https://your-render-service.onrender.com`

## API

- `GET /health`
- `GET /api/predictions/today`
- `GET /api/predictions/combo?legs=3&min_confidence=60`
- `GET /api/stats/backtest`
- `POST /api/admin/train` with `X-Admin-Key`
- `POST /api/admin/predict` with `X-Admin-Key`

## Security

Rotate any database credential pasted into chat before production. This repo only includes `.env.example`; real secrets stay in Render/Vercel environment variables.

## Notes on original bot requirements

The project was pivoted from Telegram bot delivery to a Vercel/Render website. Telegram dependencies are still listed for future extension, but the active delivery channel is the website. Fake proof, fixed signals, and deceptive winner-only history are intentionally not implemented.
