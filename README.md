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

Focused soccer growth pipeline:

```bash
# Tier-1 leagues, last 5 completed seasons by default
python backend/scripts/scale_soccer_data.py --phase tier1 --start-year 2020 --end-year 2025

# Add high-scoring sub-tiers after Tier 1 is stable
python backend/scripts/scale_soccer_data.py --phase high_scoring --start-year 2021 --end-year 2025

# Global market expansion from 2015 onward
python backend/scripts/seed_team_aliases.py
python backend/scripts/scale_soccer_data.py --phase global --start-year 2015 --end-year 2026
```

After every ~2,000 new soccer matches, run:

```bash
python backend/scripts/train_models.py
python backend/scripts/calibrate_models.py
curl -X POST "$API_URL/api/admin/backtest" -H "X-Admin-Key: $ADMIN_API_KEY"
```

Watch Brier score and log loss trend down before trusting accuracy claims.

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
- Required Env Vars: `PYTHON_VERSION=3.11.9`, `DATABASE_URL`, `ADMIN_API_KEY`, `APP_ENV=production`, `ENABLE_SCHEDULER=true`, `CORS_ORIGINS`
- Live API Env Vars: for your current setup, use `API_FOOTBALL_KEY` from API-SPORTS for fixtures and `THE_ODDS_API_KEY` from The Odds API for betting odds. Basketball is optional.
  - `API_SPORTS_KEY`: one API-SPORTS key used for both football and basketball ingestion.
  - `API_FOOTBALL_KEY`: football-only API-SPORTS/API-Football key. Used before `API_SPORTS_KEY` for soccer.
  - `API_BASKETBALL_KEY`: basketball-only API-SPORTS/API-Basketball key. Used before `API_SPORTS_KEY` for basketball.
  - `THE_ODDS_API_KEY`: odds key from `https://dash.the-odds-api.com`; used for football 1X2 odds.
  - `THE_ODDS_API_SPORT_KEYS`: comma-separated The Odds API soccer sport keys to pull odds for.
  - `LIVE_INGEST_DAYS=7`: number of upcoming days ingested by the scheduler.

### Where to get the live sports API keys

The current live ingestion uses two providers:

- Football fixtures: API-SPORTS/API-Football at `https://v3.football.api-sports.io`
- Football odds: The Odds API at `https://api.the-odds-api.com/v4/sports/{sport_key}/odds`
- Basketball games: API-SPORTS/API-Basketball at `https://v1.basketball.api-sports.io`
- API-SPORTS auth header used by the code: `x-apisports-key`
- The Odds API auth is passed as query param: `apiKey`

Get fixture keys from API-SPORTS:

1. Go to `https://dashboard.api-football.com/` or `https://api-sports.io/`.
2. Create/login to your API-SPORTS account.
3. Subscribe to **API-Football** for football fixtures.
4. Subscribe to **API-Basketball** if you want basketball ingestion.
5. Copy the API key from the dashboard.
6. In Render, open your backend service → **Environment** → add either:
   - `API_FOOTBALL_KEY=your_api_football_key` for football fixtures, or
   - `API_SPORTS_KEY=your_api_sports_key` if the same key has access to both products, or
   - `API_FOOTBALL_KEY=your_football_key` and `API_BASKETBALL_KEY=your_basketball_key` if you manage them separately.

Get odds keys from The Odds API:

1. Go to `https://dash.the-odds-api.com`.
2. Copy your API key.
3. In Render, add `THE_ODDS_API_KEY=your_the_odds_api_key`.
4. Confirm your plan supports soccer `h2h` odds for the leagues you want.

For a football-only Render deployment, use:

```text
API_FOOTBALL_KEY=your_api_football_key
THE_ODDS_API_KEY=your_the_odds_api_key
THE_ODDS_API_SPORT_KEYS=soccer_epl,soccer_spain_la_liga,soccer_italy_serie_a,soccer_germany_bundesliga,soccer_france_ligue_one,soccer_uefa_champs_league
LIVE_INGEST_DAYS=7
```

With `ENABLE_SCHEDULER=true`, the daily scheduler calls `backend/app/scraper/loaders.py::ingest_api_football_fixtures(..., include_odds=True)`, which fetches fixtures from API-SPORTS and then fetches football `h2h` odds from The Odds API. Those 1X2 odds are matched to fixtures by date/home/away team and saved as `home_odds`, `draw_odds`, and `away_odds`, then used by the soccer model features: `home_implied`, `draw_implied`, `away_implied`, and `odds_margin`.

For production frontend access, also set:

- Backend Render `CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:3000`
- Vercel frontend `NEXT_PUBLIC_API_URL=https://your-render-service.onrender.com`

Do not put API keys in the frontend or commit them to Git. Keep sports API keys only in Render backend environment variables.

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
- `POST /api/admin/backtest` with `X-Admin-Key`
- `POST /api/admin/team-aliases` with `X-Admin-Key`
- `GET /api/admin/team-aliases` with `X-Admin-Key`
- `GET /api/admin/odds-snapshots` with `X-Admin-Key`
- `POST /api/admin/odds-snapshots` with `X-Admin-Key`

## Data quality and market proof

- Team identity is now backed by `teams` and `team_aliases` tables. CSV ingestion
  resolves raw names through database aliases first, then static aliases, and then
  creates a canonical self-alias for unknown teams.
- Prediction generation captures fixture odds into `odds_snapshots` at the moment
  a pick is created/published. This is the foundation for future ROI and closing
  line value tracking.
- Closing odds can be added with `POST /api/admin/odds-snapshots`. Public stats
  include early ROI/CLV summaries for supported settled markets.

## Database migrations

This project is migration-ready with Alembic. For existing production databases,
run migrations before deploying schema-dependent code:

```bash
cd backend
alembic upgrade head
```

If you are bootstrapping an empty database for the first time, you can also run:

```bash
python backend/scripts/ensure_schema.py
```

Local MVP startup still calls `create_all()` as a safety net, but production
schema changes should be handled through Alembic revisions.

## Security

Rotate any database credential pasted into chat before production. This repo only includes `.env.example`; real secrets stay in Render/Vercel environment variables.

## Notes on original bot requirements

The project was pivoted from Telegram bot delivery to a Vercel/Render website. Telegram dependencies are still listed for future extension, but the active delivery channel is the website. Fake proof, fixed signals, and deceptive winner-only history are intentionally not implemented.
