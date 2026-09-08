# REEDS Production Diagnosis — 2026-09-08

Frontend: `https://reeds-phi.vercel.app`
Backend (Render, live): `https://reeds-phj1.onrender.com`
Backend (Fly, DEAD): `https://reeds.fly.dev` — NXDOMAIN, unreachable from multiple networks

## A. Config inspection (`render.yaml` + env)

- Root `render.yaml` (the file Render uses) had **NO `envVars` section** — a service created from
  this blueprint would start with defaults only: enforcement of `DATABASE_URL` in production, the
  scheduler, all provider keys, CORS, etc. This was a reproducibility gap below the live service.
- `backend/render.yaml` (`loyal-edge-backend`) carries the full env blueprint and is now mirrored
  into root `render.yaml` (17 env vars added).
- The **live** Render instance is correctly configured: `/api/fixtures/status` reports the
  following configured providers as `true`: api_football (API_SPORTS/API_FOOTBALL), sportmonks,
  football_data_org, bzzoiro, openfoot, fixture_download, sporting_events, allsportsapi,
  thesportsdb. Only `apifootball_com:false` (no API_FOOTBALL_COM_KEY set anywhere).
- `APP_ENV=production` is implied by the fact that providers ran and the SQLite/Postgres guard
  passed (`/ready` → `{"ok":true,"ready":true,"database":"ok"}`).
- **Could not read Render env values directly** (no Render dashboard/API credentials). Secret-ness
  was honored — nothing secret is reproduced here.
- **Action required by owner**: confirm in the Render dashboard that `DATABASE_URL` points at the
  NEW Neon production database (the running service does use a live PostgreSQL; only the exact
  target is unverifiable without dashboard access).

## B. Scheduler & jobs

- `ENABLE_SCHEDULER=true` ⇒ `app.main:on_startup` → `_finish_startup_once()` → `start_scheduler()`
  after the DB returns (graceful degradation plus a 120 s recovery loop for Postgres).
- `start_scheduler()` parks the periodic jobs behind a **cross-instance PostgreSQL advisory lock**
  (`0x52454544535_01`) and a `SCHEDULE_VIA_CRON` skip flag, so exactly one instance ingests.
- Jobs (all present, `max_instances=1`): 2 h `run_lightweight_refresh` (+30 s startup refresh),
  6 h public football coverage, 15 min score/odds sync, 1 min live events, 10 min live prediction
  heartbeat, 5 min learning watch, 30 min value scan.
- The pipeline demonstrably ran **on Render itself**: 409 real fixtures in the 7-day window
  (source mix: flashscore web 216, football_data_org 126, thesportsdb 27, livescore 20,
  sportmonks 11, allsportsapi 9), 24 leagues, 53 fixtures with odds, 6 completed with scores,
  and settled prediction outcomes — none of that exists without the scheduler/providers running.

## C. Provider availability & health

- Live provider test (external, free tier): TheSportsDB public API returned 3 real baseball
  events for 2026-09-09 — free-tier providers are reachable and returning data.
- `configured_providers` (from `/api/fixtures/status`, live): 9/10 enabled as listed in A; only
  apifootball_com is unconfigured. TheSportsDB coverage is inherently thin (free tier).
- No provider keys are exposed; provider calls are executed only with the keys already stored in
  Render (couldn't and shouldn't be read). `flashscore` web scraping is producing the largest
  share (216 rows) — the dominant source, which is low-quality/noisy (corrupted team names).

## D. Database & row counts (live production, 7-day window, excludes `coverage_seed`)

- `fixtures`: 409 in window / 24 leagues / 53 with odds / 6 finished with scores.
- Sport mix: soccer 166, american_football 77, baseball 56, basketball 45, tennis 38,
  handball 17, hockey 6, cricket 4.
- `predictions`: none currently published (`/api/predictions/today` → `[]`) — expected, see G.
- `model_versions`: 2 restored rows (soccer id 19, american_football id 20), sample_size 0,
  inactive; `backtest_runs`: none.
- `market_evidence`: 10 (sport, market) rows exist; each has settled = 1, all
  `publication_blocked:true`.
- `odds_snapshots`: >0 implied by 53 fixtures with odds and the evidence/odds flow.
- Overall: the production DB is **NOT fresh/empty** — it contains real ingested data.

## E. Controlled ingestion

- The public `/api/predictions/today` self-heal was exercised against production (it queues a
  prediction build when upcoming fixtures exist — the app's own controlled path).
- Full controlled ingestion endpoints exist but require `X-Admin-Key`:
  - `POST /api/admin/ingest-live` (all providers; `sport=all`)
  - `POST /api/admin/refresh-board` (ingest free-tier + seed + predictions)
  - `POST /api/admin/coverage-seed`, `POST /api/admin/ingest-free` (historical, for training)
- Owner action (run from a machine with the key, unquoted secrets intentionally not echoed):
  `curl -s -X POST -H "X-Admin-Key: $env:ADMIN_API_KEY" https://reeds-phj1.onrender.com/api/admin/refresh-board`

## F. Fixture visibility

- Live backend serves fixtures: `/api/fixtures/upcoming` returns real fixtures for 2026-09-09+
  (e.g., football_data_org Championship/UCL matches). Feed health is `active` (409 ≥ floor 300).
- The **deployed frontend never talks to this backend**: it is built with
  `process.env.NEXT_PUBLIC_API_URL || "https://reeds.fly.dev"` (`frontend/lib/api.ts`), and
  `reeds.fly.dev` is **NXDOMAIN/dead** (verified locally and from an independent network).
- Conclusion: the frontend's 0 fixtures/0 leagues/0 odds/0 today are the result of every request
  failing against a dead domain — the ingestion side works.

## G. Prediction generation & market gate

- `generate_today_predictions` runs in the scheduler loop; outcomes are being **settled** (10
  settled, 7 won, 3 lost, per `/api/stats/ai-learning`).
- Every market has only ~1 settled outcome, far below the **required ≥20 settled** before public
  publication (`market_evidence.publication_blocked=true`, `insufficient settled sample (1 < 20)`).
- `0 AI picks` is therefore the **correct designed behavior** right now, not a bug. Raising
  sample size requires time + settlement cycles (models also need completed fixtures; training
  is a separate step and can legitimately wait).
- No thresholds were lowered and no market gate/bypass code was touched.

## H. Root-cause diagnosis

1. **Primary (why all numbers read 0):** the deployed frontend targets the dead `reeds.fly.dev`.
   The live Render backend is healthy and full of real data. Fix = point the frontend at Render.
2. **Secondary:** the deployed Render build is **stale** relative to repo HEAD — it 404s on
   `/api/stats/summary` (added in 298a010) while `/api/stats/backtest` works. It predates
   298a010 and also the community POST endpoints (d5e6e8d). Requires a manual redeploy.
3. **Tertiary:** root `render.yaml` was missing the env blueprint (now fixed) — no new service
   should be created from it as-is.
4. `AI picks = 0` is by-design (market gate) until ≥20 settled outcomes per market accumulate.

## I. Recommended fixes & deployment

Fixes applied in this change set:
- `render.yaml`: added the full `envVars` block (parity with `backend/render.yaml`, 17 vars:
  PYTHON_VERSION, APP_ENV=production, ENABLE_SCHEDULER=true, DATABASE_URL, ADMIN_API_KEY,
  CORS_ORIGINS, API_SPORTS_KEY, API_FOOTBALL_KEY, API_FOOTBALL_COM_KEY, SPORTMONKS_API_KEY,
  FOOTBALL_DATA_API_KEY, API_BASKETBALL_KEY, THE_ODDS_API_KEY, THE_ODDS_API_SPORT_KEYS,
  LIVE_INGEST_DAYS=7, MIN_TRAINING_ROWS=20, MODEL_DIR=data/models).
- `frontend/lib/api.ts`: default `API_URL` changed from dead `https://reeds.fly.dev` to live
  `https://reeds-phj1.onrender.com` (env var `NEXT_PUBLIC_API_URL` still overrides).

Steps to go live:
1. Render → REEDS service → **Manual Deploy / Deploy latest commit** (picks up the new code,
   incl. `/api/stats/summary` and community POST endpoints).
2. Verify Render env (`DATABASE_URL` = new Neon, `CORS_ORIGINS` contains
   `https://reeds-phi.vercel.app`).
3. Vercel reeds-phi: optionally set `NEXT_PUBLIC_API_URL=https://reeds-phj1.onrender.com` in
   project env, then redeploy (the code default now also targets Render, so a plain redeploy
   works without the env var).
4. Confirm on the live frontend: fixtures ~400+, leagues 24, odds count 53, feed `active`.
5. Owner-only, from the dashboard key:
   `curl -s -X POST -H "X-Admin-Key: <key>" https://reeds-phj1.onrender.com/api/admin/refresh-board`

## Verification (local)
- `python -m pytest backend/tests -q` → **42 passed**
- `python -m compileall -q backend` → exit 0
- `render.yaml` parsed as valid YAML (17 env vars).

## Commit
- `render.yaml` — add missing envVars blueprint
- `frontend/lib/api.ts` — point default API URL at the live Render backend
- `PRODUCTION_DIAGNOSIS.md` — this report

(No secrets are included in any of the above.)