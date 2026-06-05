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
python backend/scripts/load_csv_data.py --sport soccer --path data/raw/E0.csv --league EPL --season 2024
```

Load basketball/NBA CSV:

```bash
python backend/scripts/load_csv_data.py --sport basketball --path data/raw/nba_2010_2024.csv --league NBA --season 2010-2024
```

Download football-data.co.uk history from 2000 through 2024/25:

```bash
python backend/scripts/download_historical_data.py --start-year 2000 --end-year 2025 --leagues EPL,LA_LIGA,SERIE_A,BUNDESLIGA,LIGUE_1
```

Download extra GitHub raw CSVs with a manifest:

```csv
sport,league,season,url,filename
basketball,NBA,2010-2024,https://raw.githubusercontent.com/example/nba.csv,nba_2010_2024.csv
```

```bash
python backend/scripts/download_historical_data.py --manifest data/raw/manifest.csv
```

Bulk feed everything under `data/raw`:

```bash
python backend/scripts/bulk_feed_data.py --root data/raw --sport auto --season Historical
```

Recommended model feeding order:

```text
1. Put football-data.co.uk CSVs in data/raw/football/
2. Put footballcsv/Kaggle football exports in data/raw/football/
3. Put NBA/Kaggle/GitHub basketball CSVs in data/raw/basketball/
4. Load each CSV with backend/scripts/load_csv_data.py
5. Or bulk-load folders with backend/scripts/bulk_feed_data.py
6. Run python backend/scripts/train_models.py
7. Run POST /api/admin/predict to refresh customer picks
```

Important model-feeding rules:

- Keep football and basketball data separate by sport.
- Do not mix future results into training rows used to predict earlier matches.
- Normalize team names before serious training, otherwise one club may appear as multiple teams.
- Bigger history helps, but clean history matters more than messy volume.
- The prediction model and chatbot-style wording are separate systems: match data trains picks; curated writing templates control customer-facing explanations.

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
