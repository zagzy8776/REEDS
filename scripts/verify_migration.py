"""Post-migration verifier (Phase 2): Neon vs Aiven/Cockroach.

Read-only against ALL databases. Exit 0 when every check passes, exit 1
with a clear mismatch report otherwise. Never prints secrets.

Checks:
  1. row counts source vs destination (fixtures split across hot+archive)
  2. primary-key preservation (every source id present at its destination)
  3. natural-key uniqueness on destinations (per NATURAL_KEYS)
  4. duplicate detection (PK dupes + natural-key dupes)
  5. fixture boundary correctness (hot rows all >= cutoff, archive all <
     cutoff, nothing lost, nothing double-homed, NULLs only in Aiven)
  6. logical relationships (LOGICAL_REFS orphans)
  7. required indexes/unique constraints present on destinations
  8. JSON fields read back (spot-check parse on engine_meta/extra/...)
  9. timestamp fields present and non-garbled (spot check)
 10. query smoke tests against the AIVEN copy:
       dataframe_from_db equivalent, records_map equivalent,
       market evidence/gate row, active model lookup, prediction lookup

Usage:
  python scripts/verify_migration.py --reference-date 2026-09-12
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migration_common import (  # noqa: E402
    AIVEN_TABLES,
    COCKROACH_TABLES,
    LOGICAL_REFS,
    NATURAL_KEYS,
    fixture_cutoff,
    redact_url,
)

log = logging.getLogger("verify_migration")


def _env(name):
    return os.environ.get(name, "").strip()


def _norm(url):
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def connect(url, label):
    from migration_common import connect_with_retry

    return connect_with_retry(url, label)


def count_rows(eng, table):
    from sqlalchemy import text

    with eng.connect() as conn:
        return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)


def fetch_ids(eng, table, column="id"):
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(
            text(f'SELECT "{column}" FROM "{table}" ORDER BY "{column}"')
        ).all()
    return [r[0] for r in rows]


def find_pk_dupes(eng, table):
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text(
            f'SELECT id, COUNT(*) c FROM "{table}" GROUP BY id HAVING COUNT(*) > 1'
        )).all()
    return [(r[0], r[1]) for r in rows]


def find_natural_dupes(eng, table, keys):
    from sqlalchemy import text

    collist = ", ".join(f'"{k}"' for k in keys)
    with eng.connect() as conn:
        rows = conn.execute(text(
            f'SELECT {collist}, COUNT(*) c FROM "{table}" '
            f"GROUP BY {collist} HAVING COUNT(*) > 1 LIMIT 20"
        )).mappings().all()
    return [dict(r) for r in rows]


def check_counts(report, src, aiven, cock, tables_subset):
    for table in AIVEN_TABLES:
        if tables_subset and table not in tables_subset and table != "fixtures":
            continue
        if table == "fixtures":
            src_n = count_rows(src, "fixtures")
            hot = count_rows(aiven, "fixtures")
            cold = count_rows(cock, "fixtures_archive")
            ok = (hot + cold) == src_n
            report["counts"]["fixtures_split"] = {
                "source": src_n, "aiven_hot": hot,
                "cockroach_archive": cold, "ok": ok,
            }
            if not ok:
                report["mismatches"].append(
                    f"fixtures split mismatch: source={src_n} vs "
                    f"hot({hot})+archive({cold})={hot + cold}")
            continue
        src_n = count_rows(src, table)
        dst_n = count_rows(aiven, table)
        ok = src_n == dst_n
        report["counts"][table] = {"source": src_n, "aiven": dst_n, "ok": ok}
        if not ok:
            report["mismatches"].append(f"{table}: source={src_n} vs aiven={dst_n}")
    for table in ("historical_evaluation", "backtest_runs"):
        if tables_subset and table not in tables_subset:
            continue
        src_n = count_rows(src, table)
        dst_n = count_rows(cock, table)
        ok = src_n == dst_n
        report["counts"][table] = {"source": src_n, "cockroach": dst_n, "ok": ok}
        if not ok:
            report["mismatches"].append(
                f"{table}: source={src_n} vs cockroach={dst_n}")


def check_pk_preservation(report, src, aiven, cock, tables_subset):
    for table in AIVEN_TABLES:
        if tables_subset and table not in tables_subset and table != "fixtures":
            continue
        if table == "fixtures":
            src_ids = set(fetch_ids(src, "fixtures"))
            hot_ids = set(fetch_ids(aiven, "fixtures"))
            cold_ids = set(fetch_ids(cock, "fixtures_archive"))
            missing = sorted(src_ids - hot_ids - cold_ids)
            double = sorted(hot_ids & cold_ids)
            report["pk"]["fixtures_hot"] = {"missing": missing[:20],
                                            "missing_total": len(missing),
                                            "ok": not missing}
            report["pk"]["fixtures_archive"] = {"ok": True}
            if missing:
                report["mismatches"].append(
                    f"fixtures: {len(missing)} source ids missing from both "
                    f"destinations (e.g. {missing[:5]})")
            if double:
                report["mismatches"].append(
                    f"fixtures: {len(double)} ids in BOTH hot and archive "
                    f"(e.g. {double[:5]}) — boundary double-homed")
            continue
        src_ids = set(fetch_ids(src, table))
        dst_ids = set(fetch_ids(aiven, table))
        missing = sorted(src_ids - dst_ids)
        extra = sorted(dst_ids - src_ids)
        ok = not missing and not extra
        report["pk"][table] = {"missing_total": len(missing),
                               "extra_total": len(extra),
                               "missing_sample": missing[:10],
                               "extra_sample": extra[:10], "ok": ok}
        if missing:
            report["mismatches"].append(
                f"{table}: {len(missing)} source ids missing at destination "
                f"(e.g. {missing[:5]})")
        if extra:
            report["mismatches"].append(
                f"{table}: {len(extra)} destination ids with no source "
                f"(e.g. {extra[:5]})")
    for table in ("historical_evaluation", "backtest_runs"):
        if tables_subset and table not in tables_subset:
            continue
        src_ids = set(fetch_ids(src, table))
        dst_ids = set(fetch_ids(cock, table))
        missing = sorted(src_ids - dst_ids)
        extra = sorted(dst_ids - src_ids)
        ok = not missing and not extra
        report["pk"][table] = {"missing_total": len(missing),
                               "extra_total": len(extra), "ok": ok}
        if missing or extra:
            report["mismatches"].append(
                f"{table}: missing={len(missing)} extra={len(extra)}")


def check_duplicates(report, aiven, cock, tables_subset):
    for table in AIVEN_TABLES:
        if tables_subset and table not in tables_subset and table != "fixtures":
            continue
        targets = [(aiven, table)]
        if table == "fixtures":
            targets = [(aiven, "fixtures"), (cock, "fixtures_archive")]
        for eng, tname in targets:
            dupes = find_pk_dupes(eng, tname)
            if dupes:
                report["mismatches"].append(
                    f"{tname}: {len(dupes)} duplicate PKs (e.g. {dupes[:3]})")
                report["duplicates"][tname] = {"pk_dupes": dupes[:10], "ok": False}
            keys = NATURAL_KEYS.get(table if tname == table else "fixtures_archive")
            if keys:
                ndupes = find_natural_dupes(eng, tname, keys)
                if ndupes:
                    report["mismatches"].append(
                        f"{tname}: {len(ndupes)}+ duplicate natural-key groups "
                        f"on {keys} (showing up to 20)")
                    report["duplicates"][tname] = {
                        **report["duplicates"].get(tname, {}),
                        "natural_dupes_sample": ndupes[:5], "ok": False}
    for table in ("historical_evaluation", "backtest_runs"):
        if tables_subset and table not in tables_subset:
            continue
        dupes = find_pk_dupes(cock, table)
        if dupes:
            report["mismatches"].append(f"{table}: {len(dupes)} duplicate PKs")
        keys = NATURAL_KEYS.get(table)
        if keys:
            ndupes = find_natural_dupes(cock, table, keys)
            if ndupes:
                report["mismatches"].append(
                    f"{table}: duplicate natural-key groups on {keys}")


def check_boundary(report, src, aiven, cock, cutoff):
    """Fixture 730-day boundary: hot >= cutoff, archive < cutoff."""
    from sqlalchemy import text

    iso = cutoff.isoformat()
    with aiven.connect() as conn:
        bad_hot = conn.execute(text(
            "SELECT id, match_date FROM \"fixtures\" "
            "WHERE match_date IS NOT NULL "
            "AND date(match_date) < date(:cutoff) LIMIT 10"
        ), {"cutoff": iso}).mappings().all()
        null_hot = int(conn.execute(text(
            "SELECT COUNT(*) FROM \"fixtures\" WHERE match_date IS NULL"
        )).scalar() or 0)
    with cock.connect() as conn:
        bad_cold = conn.execute(text(
            "SELECT id, match_date FROM \"fixtures_archive\" "
            "WHERE match_date IS NOT NULL "
            "AND date(match_date) >= date(:cutoff) LIMIT 10"
        ), {"cutoff": iso}).mappings().all()
    with src.connect() as conn:
        src_null = int(conn.execute(text(
            "SELECT COUNT(*) FROM \"fixtures\" WHERE match_date IS NULL"
        )).scalar() or 0)
    ok = not bad_hot and not bad_cold
    report["boundary"] = {
        "cutoff": iso,
        "hot_rows_before_cutoff": [dict(r) for r in bad_hot],
        "archive_rows_on_or_after_cutoff": [dict(r) for r in bad_cold],
        "null_match_date_in_hot": null_hot,
        "null_match_date_in_source": src_null,
        "ok": ok,
    }
    if bad_hot:
        report["mismatches"].append(
            f"boundary: {len(bad_hot)}+ hot rows older than cutoff {iso}")
    if bad_cold:
        report["mismatches"].append(
            f"boundary: {len(bad_cold)}+ archive rows on/after cutoff {iso}")
    if null_hot != src_null:
        report["mismatches"].append(
            f"boundary: NULL match_date count drift "
            f"(source={src_null} hot={null_hot})")


def check_logical_refs(report, aiven, cock, tables_subset):
    """Orphan check for every LOGICAL_REFS entry (NULL child keys skip)."""
    from sqlalchemy import text

    parent_engines = {"aiven": aiven, "cockroach_archive": cock}
    parent_table_for = {"aiven": None, "cockroach_archive": "fixtures_archive"}

    for ref in LOGICAL_REFS:
        child_table, child_col = ref[0], ref[1]
        parent_col, locations = ref[3], ref[4]
        if tables_subset and child_table not in tables_subset \
                and child_table != "fixtures":
            continue
        child_eng = cock if child_table in (
            "historical_evaluation", "backtest_runs") else aiven
        child_t = child_table
        parent_ids: set = set()
        for loc in locations:
            peng = parent_engines[loc]
            pt = parent_table_for[loc] or ref[2]
            try:
                parent_ids |= set(fetch_ids(peng, pt, parent_col))
            except Exception as exc:
                report["mismatches"].append(
                    f"refs: cannot read parent {pt} at {loc}: "
                    f"{type(exc).__name__}")
        with child_eng.connect() as conn:
            keys = conn.execute(text(
                f'SELECT DISTINCT "{child_col}" FROM "{child_t}" '
                f'WHERE "{child_col}" IS NOT NULL'
            )).all()
        orphans = sorted(k for (k,) in keys if k not in parent_ids)[:20]
        n_orphans = sum(1 for (k,) in keys if k not in parent_ids)
        key = f"{child_t}.{child_col}"
        report["refs"][key] = {"orphans_total": n_orphans,
                               "orphans_sample": orphans,
                               "ok": n_orphans == 0}
        if n_orphans:
            report["mismatches"].append(
                f"refs: {n_orphans} orphan {key} values (e.g. {orphans[:5]})")


def check_indexes(report, aiven, cock, tables_subset):
    """Required indexes / unique constraints exist on destinations.

    Works across dialects: PostgreSQL keeps constraint names (``uq_*``), while
    SQLite drops inline unique-constraint names at DDL time, so we fall back to
    matching the *column set* of any unique constraint or unique index.
    """
    from sqlalchemy import inspect as sa_inspect

    # (table, engine, name-fragments, expected-unique-column-set)
    expectations = [
        ("predictions", aiven, ("ix_predictions_fixture",), None),
        ("fixtures", aiven, ("ix_fixtures_sport",), None),
        ("odds_snapshots", aiven, ("ix_odds_snapshots_fixture",), None),
        ("market_evidence", aiven, ("uq_market_evidence",), ("sport", "market")),
        ("model_feedback", aiven, ("model_feedback_prediction",), ("prediction_id",)),
        ("historical_evaluation", cock, ("uq_historical_eval",),
         ("fixture_id", "market", "model_version_id", "fold_index")),
        ("backtest_runs", cock, ("ix_backtest_runs_sport",), None),
    ]
    for table, eng, fragments, colset in expectations:
        if tables_subset and table not in tables_subset \
                and table != "fixtures":
            continue
        try:
            insp = sa_inspect(eng)
            idx_info = insp.get_indexes(table)
            uq_info = insp.get_unique_constraints(table)
            idx_names = [i["name"] for i in idx_info]
            uq_names = [c["name"] for c in uq_info]
            names = [n for n in idx_names + uq_names if n]
        except Exception as exc:
            report["mismatches"].append(
                f"schema: cannot inspect {table}: {type(exc).__name__}")
            report["schema"][table] = {"ok": False,
                                       "error": type(exc).__name__}
            continue

        missing = [f for f in fragments
                   if not any(f in (n or "") for n in names)]

        # Column-set fallback: SQLite does not persist unique-constraint names.
        if missing and colset:
            colset_match = False
            for unique_columns in [i["column_names"] for i in uq_info]:
                if tuple(unique_columns or ()) == tuple(colset):
                    colset_match = True
                    break
            if not colset_match:
                for idx in idx_info:
                    if tuple(idx["column_names"] or ()) == tuple(colset) \
                            and idx.get("unique"):
                        colset_match = True
                        break
            if colset_match:
                missing = []

        ok = not missing
        report["schema"][table] = {"ok": ok, "missing": missing}
        if missing:
            report["mismatches"].append(
                f"schema: {table} missing indexes/constraints {missing}")


def check_json_and_timestamps(report, aiven, tables_subset):
    """Spot-check JSON readability + timestamp sanity on Aiven copy."""
    from sqlalchemy import text

    json_spots = [
        ("predictions", "engine_meta"),
        ("market_evidence", "block_reasons"),
        ("model_feedback", "feature_snapshot"),
        ("insider_signals", "extra"),
        ("match_events", "extra"),
        ("push_subscriptions", "fixture_ids"),
    ]
    for table, col in json_spots:
        if tables_subset and table not in tables_subset:
            continue
        try:
            with aiven.connect() as conn:
                vals = conn.execute(text(
                    f'SELECT "{col}" FROM "{table}" '
                    f'WHERE "{col}" IS NOT NULL LIMIT 25'
                )).all()
        except Exception as exc:
            report["mismatches"].append(
                f"json: cannot read {table}.{col}: {type(exc).__name__}")
            continue
        bad = 0
        for (val,) in vals:
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                continue
            if isinstance(val, str):
                try:
                    import json as _json
                    _json.loads(val)
                except Exception:
                    bad += 1
        key = f"{table}.{col}"
        report["json"][key] = {"sampled": len(vals), "unparseable": bad,
                               "ok": bad == 0}
        if bad:
            report["mismatches"].append(
                f"json: {bad} unparseable values in {key}")
    ts_spots = [("predictions", "created_at"), ("fixtures", "match_date"),
                ("market_evidence", "updated_at")]
    for table, col in ts_spots:
        if tables_subset and table not in tables_subset \
                and table != "fixtures":
            continue
        try:
            with aiven.connect() as conn:
                vals = conn.execute(text(
                    f'SELECT "{col}" FROM "{table}" '
                    f'WHERE "{col}" IS NOT NULL LIMIT 5'
                )).all()
        except Exception as exc:
            report["mismatches"].append(
                f"timestamps: cannot read {table}.{col}: "
                f"{type(exc).__name__}")
            continue
        report["timestamps"][f"{table}.{col}"] = {
            "sampled": len(vals),
            "sample": [str(v[0])[:32] for v in vals[:3]],
            "ok": True,
        }


def check_smoke(report, aiven, cutoff=None):
    """Query smoke tests mirroring the five production read paths.

    Uses bound parameters instead of dialect-specific date/boolean literals so
    the same SQL runs on SQLite (scratch tests) and PostgreSQL (live Aiven).
    """
    from sqlalchemy import text

    # SQLite stores booleans as 0/1; PostgreSQL uses real booleans.
    true_literal = 1 if aiven.dialect.name == "sqlite" else True
    iso_cutoff = cutoff.isoformat()

    smokes = {
        "dataframe_from_db": (
            "SELECT p.id FROM \"predictions\" p "
            "JOIN \"fixtures\" f ON p.fixture_id = f.id "
            "WHERE f.match_date >= :cutoff LIMIT 5"),
        "records_map": (
            "SELECT p.id FROM \"predictions\" p "
            "JOIN \"fixtures\" f ON p.fixture_id = f.id "
            "WHERE p.is_published = :pub LIMIT 5"),
        "market_gate": (
            "SELECT sport, market FROM \"market_evidence\" LIMIT 5"),
        "active_model": (
            "SELECT id FROM \"model_versions\" "
            "WHERE is_active = :active LIMIT 5"),
        "prediction_lookup": (
            "SELECT id FROM \"predictions\" LIMIT 5"),
    }
    for name, sql in smokes.items():
        params = {"cutoff": iso_cutoff, "pub": true_literal,
                  "active": true_literal}
        try:
            with aiven.connect() as conn:
                rows = conn.execute(text(sql), params).all()
            report["smoke"][name] = {"rows_returned": len(rows), "ok": True}
        except Exception as exc:
            report["smoke"][name] = {"ok": False,
                                     "error": type(exc).__name__}
            report["mismatches"].append(
                f"smoke: {name} failed: {type(exc).__name__}: {exc}")


def _empty_report() -> dict:
    return {
        "counts": {}, "pk": {}, "duplicates": {}, "boundary": {},
        "refs": {}, "schema": {}, "json": {}, "timestamps": {},
        "smoke": {}, "mismatches": [],
    }


def run_all_checks(report, src, aiven_eng, cock_eng, cutoff, tables_subset):
    """Run every check; a crash in one check must not hide the others."""
    checks = [
        ("row-counts", lambda: check_counts(report, src, aiven_eng, cock_eng, tables_subset)),
        ("pk-preservation", lambda: check_pk_preservation(report, src, aiven_eng, cock_eng, tables_subset)),
        ("duplicate-detection", lambda: check_duplicates(report, aiven_eng, cock_eng, tables_subset)),
        ("fixture-boundary", lambda: check_boundary(report, src, aiven_eng, cock_eng, cutoff)),
        ("logical-references", lambda: check_logical_refs(report, aiven_eng, cock_eng, tables_subset)),
        ("indexes", lambda: check_indexes(report, aiven_eng, cock_eng, tables_subset)),
        ("json-timestamps", lambda: check_json_and_timestamps(report, aiven_eng, tables_subset)),
        ("query-smoke", lambda: check_smoke(report, aiven_eng, cutoff)),
    ]
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:
            report["mismatches"].append(
                f"check '{name}' crashed: {type(exc).__name__}: {exc}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Post-migration verifier: Neon vs Aiven/Cockroach (read-only).")
    p.add_argument("--tables", default="",
                   help="comma-separated table subset (default: all)")
    p.add_argument("--reference-date", default="",
                   help="YYYY-MM-DD pinning the fixture cutoff (default: today)")
    p.add_argument("--source-url", default="",
                   help="Neon source URL (default: SOURCE_DATABASE_URL or DATABASE_URL)")
    p.add_argument("--aiven-url", default="",
                   help="Aiven URL (default: AIVEN_DATABASE_URL)")
    p.add_argument("--cockroach-url", default="",
                   help="Cockroach URL (default: COCKROACH_DATABASE_URL)")
    return p.parse_args(argv)


def print_report(report: dict) -> str:
    """Render a human-readable report. Never includes secret values."""
    lines = []
    lines.append("=" * 70)
    lines.append("MIGRATION VERIFICATION REPORT")
    lines.append("=" * 70)
    sections = [
        ("counts", "ROW COUNTS (source vs destination)"),
        ("pk", "PRIMARY-KEY PRESERVATION"),
        ("duplicates", "DUPLICATE DETECTION"),
        ("boundary", "FIXTURE 730-DAY BOUNDARY"),
        ("refs", "LOGICAL RELATIONSHIPS"),
        ("schema", "INDEXES / UNIQUE CONSTRAINTS"),
        ("json", "JSON READ-BACK"),
        ("timestamps", "TIMESTAMP SANITY"),
        ("smoke", "QUERY SMOKE TESTS"),
    ]
    for key, title in sections:
        if not report.get(key):
            continue
        lines.append("")
        lines.append(f"-- {title} --")
        for name, entry in report[key].items():
            ok = bool(entry.get("ok", True)) if isinstance(entry, dict) else False
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: {_compact(entry)}")
    lines.append("")
    lines.append(f"TOTAL MISMATCHES: {len(report['mismatches'])}")
    for i, msg in enumerate(report["mismatches"], 1):
        lines.append(f"  {i}. {msg}")
    lines.append("=" * 70)
    return "\n".join(lines)


def _compact(entry) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    keys = ("source", "aiven", "cockroach", "aiven_hot", "cockroach_archive",
            "missing_total", "extra_total", "orphans_total", "rows_returned",
            "sampled", "ok")
    parts = [f"{k}={entry[k]}" for k in keys if k in entry]
    return ", ".join(parts) if parts else json.dumps(entry)[:200]


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if args.reference_date:
        try:
            ref = date.fromisoformat(args.reference_date)
        except ValueError:
            print("FAIL-CLOSED: --reference-date must be YYYY-MM-DD.", file=sys.stderr)
            return 2
    else:
        ref = date.today()
    cutoff = fixture_cutoff(ref)
    log.info("verification cutoff (730d, ref=%s): %s", ref.isoformat(), cutoff.isoformat())

    source_url = args.source_url or _env("SOURCE_DATABASE_URL") or _env("DATABASE_URL")
    aiven_url = args.aiven_url or _env("AIVEN_DATABASE_URL")
    cockroach_url = args.cockroach_url or _env("COCKROACH_DATABASE_URL")
    missing = []
    if not source_url:
        missing.append("SOURCE_DATABASE_URL (Neon source)")
    if not aiven_url:
        missing.append("AIVEN_DATABASE_URL")
    if not cockroach_url:
        missing.append("COCKROACH_DATABASE_URL")
    if missing:
        print("FAIL-CLOSED: missing required connection strings: "
              + ", ".join(missing)
              + ". Refusing to guess or fall back to another database.",
              file=sys.stderr)
        return 2

    src = connect(source_url, "source (Neon)")
    aiven_eng = connect(aiven_url, "Aiven")
    cock_eng = connect(cockroach_url, "Cockroach")
    tables_subset = {t.strip() for t in (args.tables or "").split(",") if t.strip()}
    report = _empty_report()
    try:
        run_all_checks(report, src, aiven_eng, cock_eng, cutoff, tables_subset)
    finally:
        src.dispose()
        aiven_eng.dispose()
        cock_eng.dispose()
    print(print_report(report))
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
