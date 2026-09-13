# REEDS — Historical Bootstrap Implementation Design

Design only. No code changes, no training, no activation, no gate/schema changes
were made to produce this document. Status at time of writing: repo clean on
`main` at `bb24026`, all 48 backend tests pass.

---

## 1. Real historical data sources reachable from the REEDS runtime

All sources below are already wired into `app/scraper/free_data.py` and
`app/scraper/api_clients.py`; none require new integration code:

| Sport | Source | Coverage / volume | Real odds? | Reachable |
|---|---|---|---|---|
| soccer | football-data.co.uk (`mmz4281/{season}/{division}.csv`) | 20 leagues x 10 seasons (1516-2425), ~380/league/season -> ~60-76k rows | Yes (B365, PS, WH, Avg/Max 1X2) | free, no key |
| soccer | api-sports.io football (`GET /fixtures?date=`) | any past date, final scores | Yes (`/odds` endpoint) | needs key |
| soccer | SportMonks v3 football (`GET /fixtures/date/{d}`) | any past date | Yes | needs key |
| soccer (intl) | openfootball GitHub JSONs | World Cup/EC/CA/CL ~2016-2022, few hundred | No | free |
| basketball | NBA GitHub (NocturneBear 2010-2024 + Brescou team stats) | ~17k regular+playoff | No | free |
| basketball | api-sports.io basketball (`GET /games?date=`) | any past date | Yes (`/odds`) | needs key |
| american_football | spreadspoke `spreadspoke_scores.csv` (slieb74) | 1966->present, ~19k games | Yes: closing spread & O/U lines | free |
| tennis | JeffSackmann `atp_matches_{y}.csv` / `wta_matches_{y}.csv` | 1991->present, ~2.5k/yr ATP + ~2.3k/yr WTA | No | free |
| tennis | tennis-data.co.uk `{year}/{tour}.csv` | 2005+, finals/tour seasonal | Yes (B365/Pinnacle) | free |
| hockey | NHL API (`api-web.nhle.com`, season schedule) | 1917->present, ~1.3k/yr; recent ~9k | No | free |
| baseball | retrosheet gamelogs + chadwickbureau Games.csv | 1871->present, ~200k | No | free |
| cricket | IPL GitHub `matches.csv` | ~800 matches | No | free |
| rugby | openfootball rugby JSONs + super_rugby.csv | dozens-hundreds | No | free |

## 2. Exact mapping: source -> existing loader

- football-data.co.uk -> `ingest_football_data_co_uk(db, leagues, seasons, max_leagues)` `app/scraper/free_data.py:92`
- openfootball intl -> `ingest_openfootball(db, urls)` `free_data.py:195`
- all free in one call -> `ingest_all_free_sources(db, max_leagues=20)` `free_data.py:255` (docstring documents `POST /api/admin/ingest-free`)
- ATP -> `ingest_tennis_atp(years)` `free_data.py:391`; WTA -> `ingest_tennis_wta(years)` `:411`; tennis-data -> `ingest_tennis_data_co_uk(years)` `:435`
- NBA -> `ingest_nba_github()` `:517`; AFL -> `ingest_nfl_spreadspoke()` `:622`; NHL -> `ingest_nhl_api(seasons)` `:701`; IPL -> `ingest_ipl_github()` `:774`; rugby -> `ingest_rugby_openfootball()` `:862`; MLB -> `ingest_mlb_retrosheet()` `:921`
- api-sports football day loader -> `ingest_api_football_fixtures(db, dates)` `app/scraper/loaders.py:341-386`; basketball -> `ingest_api_basketball_games(db, dates)` `loaders.py:490-521`

Admin callers currently pass default seasons/leagues; the range-limiting problem is
only "which args to pass", **not** loader capability. All loaders are idempotent via
`upsert_fixture`.

## 3. Reuse chain — historical fixture -> evaluation (exact code paths)

A real source row (e.g. E0 2023/24: `Date=23/08/2023, HomeTeam, AwayTeam, FTHG=2,
FTAG=1, B365H/A/D`) flows:

1. `ingest_football_data_co_uk` -> `Fixture(sport="soccer", league="Premier League", season="2023/24", match_date, home_team=resolve_team_name(...), away_team=..., home_score=FTHG, away_score=FTAG, home_odds=B365H, draw_odds=B365D, away_odds=B365A, source="fdco", extra={"season_code","league_code"})` -> `upsert_fixture` (`free_data.py:146-161`). **Odds are persisted directly into Fixture columns** (`free_data.py:155-157`).
2. History frame: `dataframe_from_db(db, max_age_days=None)` (`app/services/predictions.py:106`; bounded variant `resource_guard.bounded_prediction_history(db, max_age_days=180)` `app/services/resource_guard.py:99`).
3. As-of features: `features_for_fixture(history, home_team, away_team, fixture_date=..., league=..., home_odds, draw_odds, away_odds)` (`app/ml/features.py:364`). Verified as-of-safe: `features.py:384-386` -> `hist[hist.match_date < cutoff]`. Same for `basketball_features_for_fixture` (`features.py:736-738`) and all `app/ml/advanced_features.py` masks (`match_date < match_date`).
4. Inference: reuse existing pipeline, do not reimplement:
   - `walk_forward_backtest()` `app/ml/backtest.py`
   - `EnhancedBacktester` `app/ml/backtest_enhanced.py` (odds/ROI aware)
   - `BacktestConfig` `app/ml/config.py:44-51`, `POST /api/admin/backtest` `app/api/admin.py:962-988`, `GET /api/stats/backtest` `app/main.py:170-184`, `BacktestRun` `app/db/models.py:231-244`
   - OOF training exists: `app/ml/train_oof.py`
5. Settlement: reuse `prediction_result(prediction, fixture)` `app/services/prediction_learning.py:22-73` — the single source of truth for every market (1X2, O/U, BTTS, DC, Correct Score, Spread, totals). Not re-implemented.

## 4. Why the current gate only sees settled live predictions

`compute_market_evidence` (`app/services/market_gate.py:126`) iterates
`_latest_public_rows(db)` (`market_gate.py:95-123`), which queries **only
`Prediction`** rows (active, fixture completed, `match_date` within 90-day cutoff)
— both published and internal (`is_published=False`) picks, by design (internal
picks exist to build evidence pre-publication). Historical backtest rows are never
`Prediction` rows -> structurally excluded. With only 6 completed fixtures in Neon,
soccer 1X2 shows **settled=1**.

Required change: a parallel, audited evidence source (section 6) computed by the
same math (`prediction_result`, `selected_decimal_odds`, Brier as in
`compute_market_evidence` lines 144-177) but read from `HistoricalEvaluation`
records. `market_publication_policy` (`market_gate.py:255-273`) and all threshold
constants stay untouched.

## 5. As-of / leakage-safe evaluation design

- **Structural as-of is already enforced** in feature engineering (section 3 step 3).
  Bootstrap adds a hard invariant: every evaluation passes the fixture's real
  `match_date`; a debug assert + unit test proves no feature vector references that
  fixture or any later one (`<` cutoff, no `<=`).
- **Training-window leakage (the critical one):** the production June-2026 soccer
  artifact trained on the old ~24k-row corpus. Evaluating that model on pre-June-2026
  fixtures is in-sample -> inflated, unusable. **Legitimate historical evidence must
  come from walk-forward out-of-fold evaluations** (expanding window, fixed config,
  `train_oof.py` / `EnhancedBacktester`), so every scored fixture is out-of-sample
  for its fold model. The existing June artifact may only contribute for fixtures
  dated **after** its 2026-06-22 cutoff (near-zero count) — noted in the audit trail.
- **Odds leakage:** freeze odds at ingestion. fdco B365/PS are pre-match; `_best_odds_cols`
  priority (B365->PS->WH->Avg/Max) is deterministic and pre-match (`free_data.py:81-89`).
  Never write closing lines into evaluation records post-hoc.
- **Double counting:** `HistoricalEvaluation` keyed `(fixture_id, market, model_version_id,
  fold/job_id)`; upsert on that key.
- **Same-fixture contamination:** evaluations never write to `Prediction`; they cannot
  touch the live publication balance (`MAX_OPEN_PUBLIC_PICKS`, streaks).

## 6. Proposed evidence model (live vs historical)

```
MarketEvidence (gate inputs)
|-- live_*         <- settled Prediction rows       (existing columns, unchanged semantics)
`-- historical_*   <- HistoricalEvaluation rows     (new columns, this design)
      counted in:  sample bar, overall accuracy, Brier, ROI/EV
      NEVER in:    recent-accuracy (30d) bar, loss-streak, open-public-picks
      NEVER as:    a public pick
```

Composition (constants unchanged):
- total settled = `live.settled + historical_settled`; passes sample bar when
  `total >= 20` (model-trained) or `>= 40` (analytical).
- overall accuracy = `(live.wins + historical_wins)/total_settled` vs `MIN_EMPIRICAL_ACCURACY=0.45`.
- `recent_settled >= 8` / `recent_accuracy >= 0.40` and `max_loss_streak < 5` stay
  **live-only** — the pacing mechanism: even with thousands of historical evaluations,
  a market cannot publish until live flow shows acceptable recent behavior.
- `expected_value`/`roi_units` computed from odds-bearing evaluations only;
  `historical_has_odds` distinguishes "no odds" (null) from "negative EV".
- Every gate record exposes a `provenance` breakdown so `publication_blocked=false`
  is auditable; historical rows labeled BACKTEST/BOOTSTRAP in admin/log output.

## 7. Feasibility per sport (data x model x engine)

| Sport | Data ready | Model artifact | Evidence possible now? | Verdict |
|---|---|---|---|---|
| soccer | yes (60-76k, odds) | yes (LoyalEdgeEngine soccer) | full: counts+accuracy+ROI/CLV | READY |
| american_football | yes (~19k spreadspoke, odds) | yes (AF artifact, 20-feat) | counts+accuracy+ROI on spread/totals | READY |
| basketball | yes (~17k NBA) | yes (BasketballEngine + BF artifact candidate) | counts+accuracy; ROI only if api-sports odds used | READY (counts-first) |
| tennis | yes (ATP/WTA + tennis-data odds) | no (heuristic `RecentFormToolkit` only; generic trainer exists) | partial — needs fold-trained generic model | PARTIAL, defer |
| hockey / baseball | yes (NHL ~9k; MLB ~200k) | no (heuristics only) | partial — needs models | PARTIAL, defer |
| cricket / rugby | small (~800 / hundreds) | no | no | NOT FEASIBLE now |

## 8. Corpus sizing (partial vs full)

- **Partial (recommended start, soccer only):** 8 seasons x 10 leagues
  (E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2) ~= **30k completed soccer fixtures with odds**.
  Minutes to load, idempotent, `upsert_fixture` safe on the live DB.
- **Full (phase 2):** `ingest_football_data_co_uk(max_leagues=20, seasons=ALL_SEASONS)`
  ~= **60-76k**, plus AF (~19k) and NBA (~17k) -> ~100k Fixture rows. Within the 120k
  frame cap; live prediction history stays bounded (`bounded_prediction_history`,
  180-day window).

## 9. Edge cases

- **Unresolvable outcome** (abandoned match, odd format): `prediction_result` -> `None`;
  excluded exactly as the live path excludes it (`market_gate.py:132`).
- **Pushes** (hockey/tennis): `None` outcome -> skipped (same as live).
- **No odds:** `has_odds=false`; excluded from ROI/EV, included in counts/accuracy/Brier
  (Brier from `confidence`, present on every pick).
- **Team-name drift:** `resolve_team_name(db, name, sport, source)`
  (`app/services/data_quality.py`) maps fdco abbreviations; unresolved teams are
  logged+skipped, never silently coerced.
- **League context:** `league_strength` computed from history strictly before the date.
- **Duplicates across sources:** `upsert_fixture` dedupes; evaluation key carries `source`.
- **Negative EV != "no evidence":** blocked only on accuracy/sample/streak gates.

## 10. Files that would change (implementation)

- **New:** `app/services/historical_evidence.py` (backfill orchestration + as-of
  evaluation + record writes), `app/services/evidence_pivot.py` (roll
  HistoricalEvaluation -> MarketEvidence.historical_*), migration,
  `tests/test_historical_evidence.py`.
- **Modified:** `app/services/market_gate.py` (`compute_market_evidence` merges
  live+historical; constants and `market_publication_policy` math untouched),
  `app/db/models.py` (`HistoricalEvaluation`, MarketEvidence extra columns),
  `app/api/admin.py` (read-only evidence breakdown + backfill trigger exposing
  existing `ingest_free` args), alembic versions.
- **Not changed:** `prediction_learning.prediction_result`, publication thresholds,
  `model_registry.register_model`, scheduler, `fixture_prediction`/`predictions`.

## 11. DB schema (proposed)

- **`HistoricalEvaluation`** (new): `id, fixture_id FK, sport, league, match_date,
  home_team, away_team, home_score, away_score, market, pick, confidence, edge_score,
  outcome (won/push/none), brier_score, has_odds, applied_odds, roi_units, clv,
  model_version_id, inference_mode ('walk_forward_fold'|'reuse_production'),
  fold_index, job_id, source, created_at`, unique
  `(fixture_id, market, model_version_id, fold_index)`. Never a Prediction;
  no `is_public` field.
- **`MarketEvidence`** (extend): add `historical_settled, historical_wins,
  historical_losses, historical_accuracy, historical_brier_sum, historical_brier_count,
  historical_odds_count, historical_roi_units, historical_has_odds, bootstrap_updated_at`.
  Existing `settled/wins/losses/...` keep live-only meaning.

## 12. Migration

One additive Alembic revision: create `historical_evaluation`; `ALTER TABLE
market_evidence ADD ...` (all nullable, default null). No data backfill migration
(evidence regenerated by the job). Indexes: `historical_evaluation(fixture_id)`,
`(sport, match_date)`, `(market)`. Deploy-safe on live Neon.

## 13. Expected evaluation counts after implementation

- **soccer 1X2:** ~25-30k historical evaluations (10-league partial), well above the
  20/40 bars; O/U 2.5 & BTTS ~20-25k; DC/Correct Score similar.
- **american_football:** Moneyline / Spread / Total ~= **4-5k** each (2010+ slice).
- **basketball:** Moneyline ~= **14k** (counts-only; NBA GitHub has no odds).
- **tennis/hockey/baseball:** data-ready but deferred until fold-trained models exist.

## 14. Effect on the LIVE feature quality

Today 27/94 soccer features are always-zero and live inference collapses to a constant
`(0.29, 0.2392, 0.4708)` for every fixture. Backfilling populates form, streaks, H2H,
Elo, rest-days, venue, league-strength from genuinely prior matches -> always-zero
count drops to near zero and live probabilities become fixture-discriminative.
Verifiable pre-activation: re-run the existing fixture trace after corpus load and
diff the non-degenerate probabilities; no gate change needed to observe it.

## 15. Effect on the market gate

- Soccer **1X2**: combined counting clears 20; combined accuracy computed from OOF
  pick-win rate (the pick-level OOF rate is what actually gates; report-card accuracy
  ~0.4951 is classifier-level, not the gate input). Recent/streak gates stay live-only,
  so 1X2 still cannot publish until ~8+ settled picks in the last 30 days show >=40%.
- **ROI/CLV become available for soccer** (fdco odds at ingestion) and AF spread/totals
  (spreadspoke) — `expected_value`/`roi_units` stop being null.
- Analytical markets still need 40 combined; **no threshold or constant changes.**

## 16. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Training-window inflation (reuse June artifact pre-2026-06) | Walk-forward OOF-only evidence; `inference_mode` audited; post-cutoff reuse only |
| Corpus size on API path | Live history bounded (180d window + row cap); full corpus only in worker/training jobs |
| Evidence double-count on re-run | Unique `(fixture_id, market, model_version, fold)` upsert |
| Odds contamination (post-hoc closing lines) | Odds frozen at ingestion; `_best_odds_cols` deterministic pre-match choice |
| Render 512MB OOM during walk-forward training | Run folds in the train worker / larger instance; chunked folds; not in API worker |
| Gate erosion (historical masking live decay) | recent-accuracy + loss-streak remain live-only; provenance columns audit every decision |
| Public optics ("our historical picks") | HistoricalEvaluation never public; labeled BACKTEST/BOOTSTRAP; no `is_published` path exists |

## 17. Recommended implementation order + validation gates

1. **P0 - Data backfill (no activation):** soccer fdco 8x10 via existing loader with
   explicit args -> verify counts/idempotence -> re-run fixture trace, confirm feature
   quality delta (section 14). Gate: tests green (48), counts match expectation, zero fabrications.
2. **P1 - Evidence engine:** schema (11/12) -> `historical_evidence.py` walk-forward
   evaluation writing `HistoricalEvaluation` -> `evidence_pivot` into
   `MarketEvidence.historical_*` -> admin read-only breakdown endpoint. Gate: leakage
   audit on sampled rows, OOF accuracy vs report card, resumes without inflation.
3. **P2 - Pipeline alignment:** retrain soccer/AF/basketball on the backfilled corpus
   with `train_oof.py`; calibrate (`calibration.py` Platt calibrator); bump sklearn to
   1.6.0 in image; `register_model` with **real** OOF accuracy/sample_size (fixes the
   GitHub-zero-root-caused `is_active=False` rows and empty ModelArtifact). Gate:
   `active_model_path()` resolves; live trace probabilities discriminate.
4. **P3 - Hold decision:** leave `MIN_PUBLIC_SAMPLE=20` untouched; publish soccer 1X2
   only when the **live** recent gate passes. Re-run full test suite at every step.

---

## Appendix A — Manual configuration

Secrets and platform settings that must be set by hand (never committed):

### Render (service `srv-d8h7bsd8nd3s73bvq7jg` — REEDS)

Already set, verify in Environment > Secrets & Files:

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon pooler URL |
| `ADMIN_API_KEY` | the admin key used with `/api/admin/*` |
| `ENABLE_SCHEDULER` | `true` |
| `APP_ENV` | `production` |
| `AUDIT_MODE` | `true` |
| `MODEL_DIR` | `data/models` |
| `MIN_TRAINING_ROWS` | `20` |
| `LIVE_INGEST_DAYS` | `14` |
| `CORS_ORIGINS` | `https://reeds-phi.vercel.app` |
| `PYTHON_VERSION` | `3.11.9` |

Set manually when P0/P1 land (new keys, default off so behavior is unchanged):

| Key | Value / meaning |
|---|---|
| `HISTORICAL_BOOTSTRAP_ENABLED` | `true` to enable the backfill + as-of evidence job |
| `FCDO_LEAGUES` | comma list e.g. `E0,E1,E2,E3,SP1,D1,D2,I1,I2,F1,F2` |
| `FCDO_SEASONS` | comma list e.g. `2425,2324,2223,2221,2021,1920,1819,1718` |
| `BOOTSTRAP_ODDS_SOURCE` | `B365` (deterministic pre-match odds column priority) |
| `API_SPORTS_KEY` / `API_FOOTBALL_KEY` / `API_BASKETBALL_KEY` | api-sports keys, only for API date-range backfill + real odds |
| `THE_ODDS_API_KEY` | optional; live odds/ROI verification |

### GitHub

- Model-download flow reads public GitHub releases — no token required.
- The push token is for repo writes only; do not expose it in env/config.

### Fly

- Shared IPv4 `66.241.125.7`; HTTP on port 80 works, HTTPS 443 does not — certificate
  registration is denied at the account level (`cannot register certificate`). Cannot
  be fixed by config; keep using the Render URL over HTTPS for the frontend.

### Do NOT change manually

- Publication thresholds (`MIN_PUBLIC_SAMPLE`, `MIN_RECENT_SAMPLE`,
  `MIN_EMPIRICAL_ACCURACY`, `MIN_RECENT_ACCURACY`, `MAX_LOSS_STREAK`) are code
  constants in `app/services/market_gate.py` and stay untouched per this design.