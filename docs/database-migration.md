# Neon → Aiven / Cockroach database migration (Phase 2)

Status: **tooling complete, NOT executed.** Neon remains the authoritative
production database until the migration is run (Phase 3) and every
verification check passes. This document is the operator runbook.

No secret values appear in this file. Credentials are referenced by
environment-variable name only (see also `docs/credential-rotation.md`).

## 1. Topology and ownership rules

```
                Neon (legacy DATABASE_URL)  — READ-ONLY source
                        |
                        +--> Aiven PostgreSQL   AIVEN_DATABASE_URL
                        |      hot / transactional tables (21)
                        |
                        +--> CockroachDB        COCKROACH_DATABASE_URL
                        |      cold / analytical tables (2) +
                        |      archived fixtures (fixtures_archive)
                        +--> Turso              — NOT touched by migration.
                               lease/cache keys only, no ORM rows, no
                               relational fallback (hard rule).
```

Ownership is pinned by `scripts/migration_common.py` (kept in sync with
`backend/app/db/roles.py` by tests):

| Destination | Tables |
|---|---|
| Aiven | fixtures (hot window), predictions, odds_snapshots, market_evidence, model_versions, model_artifacts (metadata-only), model_feedback, teams, team_aliases, match_events, match_lineups, insider_signals, user_predictions, community_comments, community_reactions, community_plays, win_slips, user_follows, user_subscriptions, push_subscriptions, rejected_fixtures |
| Cockroach | fixtures_archive (fixtures older than the boundary), historical_evaluation, backtest_runs |
| Turso | nothing (scheduler_lease keys only; no production writes in Phase 2) |

Safety rules enforced in code (fail-closed):
- Missing Aiven or Cockroach URL → stop. **`DATABASE_URL` is never used as a
  fallback for Cockroach** (it is only ever the Neon source).
- Source or destination connection failure → stop, exit non-zero.
- Destination table missing, or missing any source column → stop. The script
  never creates schema; Alembic baselines must be applied first.
- Hot data is never sent to Cockroach and historical data never silently sent
  to Aiven: the fixtures split is computed per row and boundary violations are
  reported by the verifier.
- Neon is never written to, deleted from, or modified — read-only connections
  only.

## 2. Fixture 730-day boundary (deterministic)

```
cutoff = reference_date − 730 days      (FIXTURES_HOT_WINDOW_DAYS = 730,
                                         equals PREDICTION_HISTORY_DAYS)
match_date >= cutoff  -> Aiven.fixtures           (hot, inclusive)
match_date <  cutoff  -> Cockroach.fixtures_archive (cold)
match_date IS NULL    -> Aiven.fixtures           (live board is the safe
                                                   side; verifier flags rows
                                                   under null_match_date)
```

`reference_date` defaults to `date.today()` but BOTH scripts accept
`--reference-date YYYY-MM-DD`. Always pass the same value to the migration and
the verifier so the boundary is reproducible. The inclusive `>=` comparison
guarantees no fixture row is lost at the edge and no row is double-homed
(a row goes to exactly one partition, decided by one comparison).

## 3. Migration order

Parents before children so logical references resolve during backfill:

1. Aiven: fixtures → teams → team_aliases → model_versions → model_artifacts
   → predictions → odds_snapshots → market_evidence → model_feedback →
   match_events → match_lineups → insider_signals → user_predictions →
   community_* → win_slips → user_follows → user_subscriptions →
   push_subscriptions → rejected_fixtures
2. Cockroach: fixtures_archive → historical_evaluation → backtest_runs

Resumability: every source table is read with keyset pagination
(`WHERE id > :last ORDER BY id ASC LIMIT :n`), so stopping and restarting
never re-reads or re-derives already-copied ranges, and
`--start-after TABLE:ID` / `--tables A,B` let an operator resume a specific
table at a specific id.

## 4. Idempotency and batching

- Batches of `--batch-size` rows (default 500); each batch is one destination
  transaction — commit per batch, rollback only the current batch on error.
- Upserts are `INSERT ... ON CONFLICT (id) DO UPDATE` with the explicit
  source `id` preserved, so PKs never change and re-runs converge to the same
  state without duplicates.
- `model_artifacts.data` is forced to `b""` (metadata-only rule from the
  Render OOM history); the pickle bytes never travel through the row stream.
- JSON columns are copied as structured values (not stringified), timestamps
  as datetime objects, binary columns as bytes — no lossy serialization.

## 5. Dry-run procedure (zero writes)

```bash
SOURCE_DATABASE_URL='<neon-readonly-url>' \
AIVEN_DATABASE_URL='<aiven-url>' \
COCKROACH_DATABASE_URL='<cockroach-url>' \
python scripts/migrate_neon_split.py --dry-run --reference-date 2026-09-12
```

`--dry-run` reads Neon fully, applies the fixture split, and logs
`[dry-run] table -> dest: would upsert N rows` per batch. **No destination
statement is executed.** Use it to sanity-check row counts and routing before
the real run. Add `--limit-rows 100` for a fast smoke pass.

## 6. Actual migration procedure (Phase 3 day — NOT run yet)

```bash
# 0) Provision Aiven + Cockroach, then apply the baselines (creates schema):
alembic -c backend/alembic/baselines/aiven_baseline_0001.py upgrade head
alembic -c backend/alembic/baselines/cockroach_baseline_0001.py upgrade head

# 1) Dry run first (see section 5). Confirm the logged plan.

# 2) Real migration (single command, per-table batches, resumable):
SOURCE_DATABASE_URL='<neon-readonly-url>' \
AIVEN_DATABASE_URL='<aiven-url>' \
COCKROACH_DATABASE_URL='<cockroach-url>' \
python scripts/migrate_neon_split.py --reference-date 2026-09-12 --batch-size 500

# 3) If interrupted, resume from the last logged cursor, e.g.:
#    ... migrate_neon_split.py --reference-date 2026-09-12 --start-after predictions:9000
```

Behavior on any failure: the current batch transaction rolls back, the script
exits non-zero, and nothing else is attempted — no fallbacks, no retries to a
different database.

## 7. Verification procedure

```bash
SOURCE_DATABASE_URL='<neon-readonly-url>' \
AIVEN_DATABASE_URL='<aiven-url>' \
COCKROACH_DATABASE_URL='<cockroach-url>' \
python scripts/verify_migration.py --reference-date 2026-09-12
```

Read-only. Checks, per table:
1. Row counts: source vs Aiven vs Cockroach (fixtures split: hot + archive =
   source).
2. PK preservation: every source `id` exists at its destination (missing /
   extra reported).
3. Natural-key uniqueness (fixtures, teams, team_aliases, market_evidence,
   model_feedback, insider_signals, match_events, match_lineups,
   user_follows, historical_evaluation, …).
4. Duplicate detection on destination PKs and natural keys.
5. Boundary correctness: no hot row in the archive, no cold row in Aiven.
6. Logical relationships (the schema has no SQL ForeignKeys; these were
   recovered by code inspection): predictions.fixture_id, odds_snapshots.
   fixture_id/prediction_id, model_feedback.prediction_id/fixture_id,
   user_predictions / match_events / match_lineups / insider_signals.
   fixture_id, team_aliases.team_id, historical_evaluation.fixture_id →
   archived fixture IDs.
7. Required indexes / unique constraints exist on the destinations.
8. JSON fields read back as structures.
9. Timestamps survive round-trip.
10. Query smoke tests against Aiven: `dataframe_from_db`-equivalent window
    query, `records_map`-equivalent join, market evidence/gate lookups,
    active-model lookup, prediction lookup.

Output is a PASS/FAIL report with a `TOTAL MISMATCHES` counter; exit code is
non-zero when any mismatch exists. Exit 0 = migration is verified complete.

## 8. Rollback procedure

- Until Phase 3 cutover, **nothing changes for production**: the app still
  reads/writes Neon via the legacy engine. "Rollback" = do nothing.
- If verification fails after a real run: fix and re-run the migration
  (idempotent upserts make this safe) or leave both targets unused — Neon is
  untouched throughout.
- Destination cleanup before a fresh re-run, if ever needed, is manual and
  explicit (TRUNCATE the affected destination tables), never automatic. The
  migration script itself never deletes.

## 9. Neon stays authoritative until verification succeeds

Neon continues serving all application traffic (Phase 1 kept every consumer
on the legacy engine). Aiven/Cockroach receive backfilled copies only. The
cutover to reading Aiven is a Phase 3 change and happens only after
`verify_migration.py` exits 0. Until then Neon is the single source of truth.

## 10. Later: production cutover (Phase 3 preview)

1. Freeze writes (maintenance window) or run a final delta pass of
   `migrate_neon_split.py` (idempotent).
2. Re-run `verify_migration.py` — must exit 0.
3. Switch Render's `DATABASE_URL` to the Aiven URL (single env change; the
   app code is role-aware from Phase 1 but still defaults to the legacy
   engine).
4. Point the historical/backtest readers at Cockroach (Phase 3 wiring).
5. Keep Neon read-only as the rollback snapshot for an agreed window; only
   then rotate/delete it per `docs/credential-rotation.md`.

## 11. Tests

`backend/tests/test_migration_common.py`, `test_migrate_neon_split.py`,
`test_verify_migration.py` (+ `migration_scratch.py` fixtures) cover:
batching, idempotency (double-run converges), dry-run writes nothing,
fixture cutoff routing incl. NULL match_date, PK preservation, logical
reference checks, missing destination rows, duplicate destination rows,
mismatch reporting, and secret redaction. All run on scratch SQLite engines —
**no live Neon/Aiven/Cockroach credentials are required or used.**
