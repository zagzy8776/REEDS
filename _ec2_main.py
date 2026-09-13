"""REEDS EC2 training worker (first AWS milestone).

Primary training path: EC2 -> full OOF ensemble (train_oof.py) -> artifacts ->
unique GitHub 'models-v*' release -> Render sync-models-safe (streamed GitHub ->
Render disk, never materialized in Render RAM) -> Render model registry.
Legacy direct upload (EC2_UPLOAD_STRATEGY=direct) posts straight to the Render
upload endpoints and is kept only as an explicit opt-in fallback.
Kaggle remains the legacy/fallback worker and is untouched.

Commands:
  validate    Config + connectivity dry-run. Nothing is trained or uploaded.
  train       Train ONE sport in this process with the production OOF ensemble,
              write the bundle + sidecar JSON, then publish + sync Render unless
              --skip-upload is given.
  upload      Publish an EXISTING artifact + matching sidecar to a GitHub
              'models-v*' release and sync Render (no training). Set
              EC2_UPLOAD_STRATEGY=direct for the legacy direct-upload path.
              --verify deserializes and structurally checks the bundle first.
  run-all     Train every sport sequentially, each in its own child process so
              the OS reclaims memory completely between sports. Never concurrent.

Secrets policy: every secret (DATABASE_URL, ADMIN_API_KEY, ...) is read ONLY
from environment variables. Values are never printed or written to logs; error
messages reference variable NAMES only.

Exit codes:
  0 success
  1 training / data failure
  2 configuration error
  3 network / upload failure
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

EXIT_OK = 0
EXIT_DATA = 1
EXIT_CONFIG = 2
EXIT_NETWORK = 3

# Env variable NAMES that the worker requires per command. Never their values.
REQUIRED_ENV = {
    "train": ("DATABASE_URL",),
    "upload": ("RENDER_URL", "ADMIN_API_KEY"),
    "validate": ("DATABASE_URL", "RENDER_URL", "ADMIN_API_KEY"),
    "run-all": (),
}

# scikit-learn must stay in 1.6.x so uploaded bundles match Render's 1.6.0.
SKLEARN_MIN = (1, 6)
SKLEARN_MAX = (1, 7)

SPORTS = (
    "soccer", "basketball",
    "tennis", "american_football", "hockey", "cricket", "rugby", "baseball",
)

_STATE = {"peak_mb": 0.0}


def _rss_mb() -> float | None:
    """Current process resident set size in MiB (Linux /proc)."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None


def _log_rss(label: str) -> None:
    rss = _rss_mb()
    print(f"[rss] {label}: {rss:.1f} MiB" if rss is not None else f"[rss] {label}: n/a", flush=True)


def _peak_rss_sampler(stop: threading.Event) -> None:
    while not stop.wait(0.5):
        rss = _rss_mb()
        if rss is not None:
            _STATE["peak_mb"] = max(_STATE["peak_mb"], rss)


def _missing(vars_ref) -> list[str]:
    names = list(vars_ref)
    return [name for name in names if not os.environ.get(name, "").strip()]


def _require_upload_env() -> list[str]:
    """Env NAMES required to ship an artifact, by strategy (returned, never values).

    github (default): GITHUB_TOKEN is needed to publish the release plus the
    Render pair for /api/admin/sync-models-safe. direct (legacy): Render only.
    """
    strategy = os.environ.get("EC2_UPLOAD_STRATEGY", "github").strip().lower()
    required = ["RENDER_URL", "ADMIN_API_KEY"]
    if strategy != "direct":
        required.insert(0, "GITHUB_TOKEN")
    return [name for name in required if not os.environ.get(name, "").strip()]


def _redact(text) -> str:
    """Mask credential material in error text before it is displayed.

    Defense-in-depth: only the DISPLAYED string is altered; the underlying
    exception object is untouched and the original handling is unchanged.
    """
    text = str(text)
    text = re.sub(r"(postgres(?:ql)?://)([^\s/]+)@", r"\1<REDACTED>@", text)
    text = re.sub(r"(password\s*=\s*[\"']?)[^\s\"']+", r"\1<REDACTED>", text)
    return text


def _setup_app_env() -> None:
    """Validate the required env, then configure the app runtime BEFORE any app import."""
    missing = _missing(REQUIRED_ENV.get(_MODE, ()))
    if missing:
        print(f"Missing required environment variable(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(EXIT_CONFIG)

    os.environ.setdefault("APP_ENV", "production")
    model_dir = os.environ.get("MODEL_DIR") or str(BACKEND_DIR / "data" / "models")
    os.environ["MODEL_DIR"] = str(Path(model_dir).resolve())
    os.environ.setdefault("MIN_TRAINING_ROWS", "200")
    Path(os.environ["MODEL_DIR"]).mkdir(parents=True, exist_ok=True)


def _render_base_url() -> str:
    return os.environ["RENDER_URL"].rstrip("/")


def _wait_for_render_ready(max_wait_seconds: int = 600) -> None:
    """Never upload into a Render instance that is restarting."""
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            import requests
            response = requests.get(f"{_render_base_url()}/ready", timeout=20)
            if response.ok and response.json().get("ready") is True:
                print(f"Render readiness confirmed (attempt {attempt})", flush=True)
                return
            print(f"Render not ready yet (HTTP {response.status_code}); waiting...", flush=True)
        except Exception as exc:
            print(f"Render readiness check failed; waiting... ({_redact(exc)})", flush=True)
        time.sleep(min(5 + attempt, 20))
    raise RuntimeError("Render did not become ready within 10 minutes")


def _rewind_files(files) -> None:
    if not isinstance(files, dict):
        return
    for value in files.values():
        handle = value[1] if isinstance(value, tuple) and len(value) >= 2 else None
        if hasattr(handle, "seek"):
            handle.seek(0)


def _post(path: str, *, timeout: int = 180, retries: int = 12, **kwargs):
    """POST to Render with restart tolerance and safe multipart retries.

    Raises RuntimeError with a clear message on failure; never prints secrets.
    """
    import requests
    headers = {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
    headers.update(kwargs.pop("headers", {}))
    files = kwargs.get("files")
    last_response = None
    for attempt in range(1, retries + 1):
        try:
            _rewind_files(files)
            response = requests.post(f"{_render_base_url()}{path}", headers=headers, timeout=timeout, **kwargs)
            last_response = response
            if response.ok:
                return response
            if response.status_code not in {502, 503, 504}:
                raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.text[:500]}")
            print(f"{path}: transient HTTP {response.status_code}; retry {attempt}/{retries}", flush=True)
        except RuntimeError:
            raise
        except Exception as exc:
            print(f"{path}: transient request error; retry {attempt}/{retries}: {_redact(exc)}", flush=True)
        if attempt < retries:
            time.sleep(min(10 * attempt, 60))
    detail = last_response.text[:500] if last_response is not None else "no response"
    status = last_response.status_code if last_response is not None else "request-error"
    raise RuntimeError(f"{path} failed after {retries} retries: HTTP {status}: {detail}")


def _read_sidecar(artifact: Path) -> dict:
    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file():
        raise RuntimeError(f"Sidecar metadata missing: {sidecar}")
    with sidecar.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Sidecar metadata is not a JSON object: {sidecar}")
    return payload


def _github_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set; required for GitHub-release publishing. "
            "Add GITHUB_TOKEN to /etc/reeds.env (Contents: Read & write on the REEDS repo)."
        )
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _publish_github_release(artifact: Path, sidecar: Path, sport: str) -> str:
    """Publish the artifact (.joblib) + sidecar (.json) to a unique GitHub release.

    Tag format 'models-v<UTC-timestamp>-<sport>' is unique per publish, which is
    what sync-models-safe / bootstrap expect ('models-v*'). The release is
    created first and deleted again if either asset upload fails so a broken
    release never becomes 'releases[0]' for /api/admin/sync-models-safe. Raises
    RuntimeError on any failure so a failed publish can never be mistaken for a
    successful Render sync. The token is only ever sent as a header; it is never
    logged.
    """
    import requests
    repo = os.environ.get("GITHUB_REPO", "zagzy8776/REEDS").strip() or "zagzy8776/REEDS"
    headers = _github_headers()
    tag = f"models-v{time.strftime('%Y%m%d%H%M%S')}-{sport}"
    creation = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers=headers,
        json={
            "tag_name": tag,
            "name": f"REEDS model: {sport}",
            "body": f"Full OOF ensemble artifact for {sport}",
            "draft": False,
            "prerelease": False,
        },
        timeout=30,
    )
    if not creation.ok:
        raise RuntimeError(f"GitHub release creation failed: HTTP {creation.status_code}")
    release = creation.json()
    release_id = release.get("id")
    upload_url = str(release.get("upload_url", "")).replace("{?name,label}", "")

    def _upload_asset(path: Path, content_type: str) -> None:
        with path.open("rb") as handle:
            uploaded = requests.post(
                f"{upload_url}?name={path.name}",
                headers={**headers, "Content-Type": content_type},
                data=handle,
                timeout=300,
            )
        if not uploaded.ok:
            raise RuntimeError(f"GitHub asset upload failed for {path.name}: HTTP {uploaded.status_code}")

    try:
        _upload_asset(artifact, "application/octet-stream")
        if sidecar.is_file():
            _upload_asset(sidecar, "application/json")
    except Exception:
        if release_id:
            try:
                requests.delete(
                    f"https://api.github.com/repos/{repo}/releases/{release_id}",
                    headers=headers,
                    timeout=30,
                )
            except Exception:
                pass
        raise
    return tag


def _upload_direct(artifact: Path, sport: str, model_type: str, accuracy: float, sample_size: int) -> None:
    """Legacy opt-in strategy (EC2_UPLOAD_STRATEGY=direct): stream to Render's endpoints."""
    _wait_for_render_ready()

    with artifact.open("rb") as handle:
        response = _post(
            "/api/admin/upload-model",
            timeout=300,
            retries=12,
            files={"model": (artifact.name, handle, "application/octet-stream")},
            data={
                "sport": sport,
                "model_type": model_type,
                "accuracy": str(accuracy),
                "sample_size": str(sample_size),
            },
        )
    print(f"[upload] upload-model: {response.json()}", flush=True)

    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file():
        return
    metadata_payload = dict(_read_sidecar(artifact))
    metadata_payload["sport"] = sport
    metadata_payload["filename"] = artifact.name
    import io
    with io.BytesIO(json.dumps(metadata_payload).encode("utf-8")) as buffer:
        payload = _post(
            "/api/admin/upload-model-metadata",
            timeout=60,
            retries=3,
            files={"metadata": (artifact.name.replace(".joblib", ".json"), buffer, "application/json")},
            data={"sport": sport},
        )
    print(f"[upload] upload-model-metadata: {payload.json()}", flush=True)


def _upload_artifact(artifact: Path, sport: str | None) -> None:
    """Ship one artifact + sidecar to production.

    Default (EC2_UPLOAD_STRATEGY=github): publish the artifact + sidecar to a
    unique GitHub 'models-v*' release, then ask Render to sync-models-safe. The
    53 MiB payload is streamed GitHub -> Render disk and is never materialized
    in Render RAM. Legacy (EC2_UPLOAD_STRATEGY=direct) streams straight to the
    Render upload endpoints.
    """
    artifact = artifact.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError(f"Artifact missing or empty: {artifact}")
    size_mb = artifact.stat().st_size / (1024 * 1024)
    print(f"[upload] artifact={artifact.name} size={size_mb:.1f} MiB", flush=True)
    if size_mb > 90:
        print("[upload] WARNING: artifact is near the 100 MiB upload cap; prefer EC2_UPLOAD_STRATEGY=github", flush=True)

    meta = _read_sidecar(artifact)
    target_sport = str(sport or meta.get("sport") or "").strip().lower()
    if not target_sport:
        raise RuntimeError("Could not determine sport for upload (pass --sport or provide sidecar 'sport')")
    accuracy = float(meta.get("accuracy", 0.0) or 0.0)
    sample_size = int(meta.get("sample_size", 0) or 0)
    model_type = str(meta.get("model_type") or "+".join(meta.get("model_types") or []) or "uploaded")[:120]
    if not 0.0 <= accuracy <= 1.0:
        raise RuntimeError(f"Invalid accuracy in sidecar: {accuracy}")
    if sample_size <= 0:
        raise RuntimeError(f"Invalid sample_size in sidecar: {sample_size}")

    strategy = os.environ.get("EC2_UPLOAD_STRATEGY", "github").strip().lower()
    if strategy == "direct":
        _upload_direct(artifact, target_sport, model_type, accuracy, sample_size)
        return
    if strategy != "github":
        raise RuntimeError(f"Unknown EC2_UPLOAD_STRATEGY={strategy!r}; use 'github' (default) or 'direct'")

    sidecar = artifact.with_suffix(".json")
    tag = _publish_github_release(artifact, sidecar, target_sport)
    print(f"[upload] published GitHub release {tag}", flush=True)

    _wait_for_render_ready()
    sync = _post("/api/admin/sync-models-safe", timeout=600, retries=12)
    sync_body = sync.json()
    if str(sync_body.get("status")) != "success":
        raise RuntimeError(f"Render sync-models-safe did not succeed: status={sync_body.get('status')!r}")
    print(f"[upload] sync-models-safe OK (release {tag}): {sync_body}", flush=True)


def _write_sidecar(result: dict, artifact: Path) -> None:
    sidecar = artifact.with_suffix(".json")
    payload = {
        "sport": str(result.get("sport") or artifact.name.split("_")[0]).strip().lower(),
        "model_type": result.get("model_type", "uploaded"),
        "accuracy": float(result.get("accuracy", 0.0)),
        "sample_size": int(result.get("sample_size", 0)),
        "model_types": result.get("models_trained", []),
        "runtime_versions": result.get("runtime_versions", {}),
        "filename": artifact.name,
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[sidecar] wrote {sidecar.name}", flush=True)


BUNDLE_REQUIRED_FIELDS = (
    "bundle_version",
    "sport",
    "models",
    "meta_learner",
    "features",
    "model_types",
    "weights",
    "accuracy",
    "sample_size",
    "runtime_versions",
)


def _verify_bundle(artifact: Path) -> None:
    """Deserialize an artifact locally and verify its structure before upload.

    Raises RuntimeError on any failure. Never modifies the artifact. Nothing is
    sent to Render unless this passes.
    """
    artifact = artifact.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError(f"Artifact missing or empty: {artifact}")
    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file():
        raise RuntimeError(f"Sidecar metadata missing: {sidecar}")
    try:
        import joblib
        bundle = joblib.load(str(artifact))
    except Exception as exc:
        raise RuntimeError(f"Artifact failed to deserialize with joblib: {_redact(exc)}") from exc
    if not isinstance(bundle, dict):
        raise RuntimeError(f"Artifact is not a bundle dictionary (got {type(bundle).__name__})")
    missing = [field for field in BUNDLE_REQUIRED_FIELDS if field not in bundle]
    if missing:
        raise RuntimeError(f"Artifact bundle missing expected field(s): {', '.join(missing)}")
    try:
        accuracy = float(bundle.get("accuracy", 0.0))
        sample_size = int(bundle.get("sample_size", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Artifact bundle has invalid accuracy/sample_size: {_redact(exc)}") from exc
    if not 0.0 <= accuracy <= 1.0:
        raise RuntimeError(f"Artifact bundle has invalid accuracy: {accuracy}")
    if sample_size <= 0:
        raise RuntimeError(f"Artifact bundle has invalid sample_size: {sample_size}")
    models = bundle.get("models")
    if not isinstance(models, dict) or not models:
        raise RuntimeError("Artifact bundle has no fitted 'models' mapping")
    print(f"[verify] artifact integrity OK: dict with {len(models)} fitted estimators, "
          f"sample_size={sample_size}, accuracy={accuracy:.2%}", flush=True)


def _cmd_validate(_args) -> int:
    _setup_app_env()
    print("== REEDS EC2 worker validation (dry-run; no training, no upload) ==")
    print(f"MODEL_DIR={Path(os.environ['MODEL_DIR'])}")
    print(f"upload strategy: EC2_UPLOAD_STRATEGY={os.environ.get('EC2_UPLOAD_STRATEGY', 'github')}")

    try:
        import sklearn
        version = tuple(int(part) for part in sklearn.__version__.split(".")[:2])
    except Exception as exc:
        print(f"Could not import scikit-learn: {_redact(exc)}", file=sys.stderr)
        return EXIT_CONFIG
    locked = SKLEARN_MIN <= version < SKLEARN_MAX
    print(f"scikit-learn {sklearn.__version__} (lock range {'1.6.x' if locked else 'OUT OF RANGE'})")
    if not locked:
        print("scikit-learn outside the 1.6.x lock; Render expects 1.6.0. Install backend/requirements.txt.", file=sys.stderr)
        return EXIT_CONFIG

    print("-- database connectivity --", flush=True)
    try:
        from app.db.session import SessionLocal
        from app.db.models import Fixture
        from sqlalchemy import func
        db = SessionLocal()
        try:
            completed = (
                db.query(func.lower(Fixture.sport).label("sport"), func.count(Fixture.id))
                .filter(Fixture.home_score.isnot(None), Fixture.away_score.isnot(None))
                .group_by(func.lower(Fixture.sport))
                .order_by(func.count(Fixture.id).desc())
                .all()
            )
            total_fixtures = db.query(Fixture).count()
            print(f"PostgreSQL connected: total fixtures={total_fixtures:,}")
            print("Completed rows by sport (training input):")
            for sport, count in completed:
                print(f"  {str(sport or 'unknown'):<22} {int(count):,}")
        finally:
            db.close()
    except Exception as exc:
        print(f"Database check FAILED: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        return EXIT_NETWORK

    print("-- Render connectivity --", flush=True)
    import requests
    try:
        ready = requests.get(f"{_render_base_url()}/ready", timeout=20)
        body = ready.json() if ready.ok else {}
        print(f"GET /ready -> HTTP {ready.status_code}, ready={body.get('ready')}")
        if not (ready.ok and body.get("ready") is True):
            print("Render /ready did not report ready=True", file=sys.stderr)
            return EXIT_NETWORK
    except Exception as exc:
        print(f"Render /ready check FAILED: {_redact(exc)}", file=sys.stderr)
        return EXIT_NETWORK

    try:
        status = requests.get(f"{_render_base_url()}/api/admin/training-status",
                              headers={"X-Admin-Key": os.environ["ADMIN_API_KEY"]}, timeout=20)
        print(f"GET /api/admin/training-status -> HTTP {status.status_code}")
        if status.ok:
            data = status.json()
            print(f"  ready_to_train={data.get('ready_to_train')} soccer_completed={data.get('soccer_completed')}")
            print(f"  active_models={[(m.get('sport'), round(float(m.get('accuracy', 0)) * 100, 1), m.get('rows')) for m in data.get('active_models', [])]}")
        else:
            print("Admin endpoint rejected the key (check ADMIN_API_KEY).", file=sys.stderr)
            return EXIT_NETWORK
    except Exception as exc:
        print(f"Render admin check FAILED: {_redact(exc)}", file=sys.stderr)
        return EXIT_NETWORK

    print("VALIDATION PASSED — ready to train on EC2.", flush=True)
    return EXIT_OK


def _cmd_train(args) -> int:
    _setup_app_env()
    sport = str(args.sport).strip().lower()
    if sport not in SPORTS:
        print(f"Unknown sport: {sport}. Valid: {', '.join(SPORTS)}", file=sys.stderr)
        return EXIT_CONFIG

    if not args.skip_upload:
        missing = _require_upload_env()
        if missing:
            print(f"Missing required environment variable(s) for upload: {', '.join(missing)}", file=sys.stderr)
            print("Set them before training, or pass --skip-upload to train locally only.", file=sys.stderr)
            return EXIT_CONFIG

    min_rows = int(os.environ.get("MIN_TRAINING_ROWS", "200"))

    print(f"== TRAIN {sport} (full OOF ensemble, process pid {os.getpid()}) ==", flush=True)
    print(f"MODEL_DIR={Path(os.environ['MODEL_DIR'])}", flush=True)
    _log_rss("before db load")

    try:
        import sklearn
        version = tuple(int(part) for part in sklearn.__version__.split(".")[:2])
        if not (SKLEARN_MIN <= version < SKLEARN_MAX):
            print(f"scikit-learn {sklearn.__version__} out of lock range; install backend/requirements.txt", file=sys.stderr)
            return EXIT_CONFIG
    except Exception as exc:
        print(f"scikit-learn import failed: {_redact(exc)}", file=sys.stderr)
        return EXIT_CONFIG

    from app.db.session import SessionLocal, init_db
    from app.db.models import Fixture
    from app.services.predictions import dataframe_from_db

    try:
        init_db()
    except Exception as exc:
        print(f"Database init FAILED: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        return EXIT_NETWORK

    db = SessionLocal()
    try:
        data = dataframe_from_db(db, max_age_days=None)
        coverage_seed_ids = {row[0] for row in db.query(Fixture.id).filter(Fixture.source == "coverage_seed").all()}
    finally:
        db.close()

    if data.empty:
        print("No training data returned from the database.", file=sys.stderr)
        return EXIT_DATA
    if "sport" not in data.columns:
        print("Training data has no sport column.", file=sys.stderr)
        return EXIT_DATA
    if "id" in data.columns and coverage_seed_ids:
        data = data[~data["id"].isin(coverage_seed_ids)].copy()
    data["sport"] = data["sport"].astype(str).str.strip().str.lower()
    data = data[data["home_score"].notna() & data["away_score"].notna()].copy()
    print("Completed training rows by sport:", flush=True)
    print(data["sport"].value_counts().sort_values(ascending=False).to_string(), flush=True)

    frame = data[data["sport"] == sport].copy()
    del data
    gc.collect()
    _log_rss("after sport slice (before feature creation)")
    if len(frame) < min_rows:
        print(f"{sport}: {len(frame):,} completed rows < MIN_TRAINING_ROWS={min_rows}; skipping", file=sys.stderr)
        return EXIT_DATA

    stop = threading.Event()
    sampler = threading.Thread(target=_peak_rss_sampler, args=(stop,), daemon=True)
    sampler.start()

    started = time.time()
    try:
        from app.ml.train_oof import (
            train_soccer_model_oof,
            train_basketball_model_oof,
            train_generic_sport_model_oof,
        )
        if sport == "soccer":
            trainer = train_soccer_model_oof
        elif sport == "basketball":
            trainer = train_basketball_model_oof
        else:
            def trainer(frame, _sport=sport):
                return train_generic_sport_model_oof(frame, _sport)
        print(f"TRAIN {sport}: {len(frame):,} rows — full OOF ensemble (RF + XGB + LGBM + CatBoost)", flush=True)
        result = trainer(frame)
        elapsed = time.time() - started
    except Exception as exc:
        import traceback
        print(_redact(traceback.format_exc()), file=sys.stderr)
        print(f"TRAIN {sport} FAILED: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        return EXIT_DATA
    finally:
        stop.set()
        sampler.join(timeout=2)
        _log_rss("after training")

    print(f"TRAIN {sport} OK: accuracy={result['accuracy']:.2%} rows={result['sample_size']:,} "
          f"models={','.join(result.get('models_trained', []))} time={elapsed:.0f}s peak_rss={_STATE['peak_mb']:.1f} MiB", flush=True)

    artifact = Path(result["path"]).resolve()
    if not artifact.is_file():
        print(f"Trainer reported missing artifact: {artifact}", file=sys.stderr)
        return EXIT_DATA
    print(f"[artifact] {artifact.name} = {artifact.stat().st_size / (1024 * 1024):.1f} MiB", flush=True)
    _write_sidecar(result, artifact)

    if args.skip_upload:
        print(f"[train] {sport} trained locally; upload skipped (--skip-upload). Artifact: {artifact}", flush=True)
        return EXIT_OK

    try:
        _verify_bundle(artifact)
    except Exception as exc:
        print(f"Artifact verification FAILED for {sport}; not uploading: {_redact(exc)}", file=sys.stderr)
        print(f"Artifact preserved locally: {artifact} — inspect and re-upload manually if appropriate.", file=sys.stderr)
        return EXIT_DATA

    try:
        _upload_artifact(artifact, sport)
    except Exception as exc:
        print(f"UPLOAD FAILED for {sport}: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        print(f"Artifact preserved locally: {artifact} — re-upload with: "
              f"python {SCRIPT_DIR / 'ec2_train_worker.py'} upload --sport {sport} --artifact {artifact}", file=sys.stderr)
        return EXIT_NETWORK
    return EXIT_OK


def _cmd_upload(args) -> int:
    _setup_app_env()
    sport = str(args.sport).strip().lower()
    artifact = Path(args.artifact).resolve()
    if sport not in SPORTS:
        print(f"Unknown sport: {sport}. Valid: {', '.join(SPORTS)}", file=sys.stderr)
        return EXIT_CONFIG
    missing = _require_upload_env()
    if missing:
        print(f"Missing required environment variable(s) for upload: {', '.join(missing)}", file=sys.stderr)
        return EXIT_CONFIG
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        print(f"Artifact missing or empty: {artifact}", file=sys.stderr)
        return EXIT_CONFIG
    if not artifact.name.endswith(".joblib"):
        print(f"Artifact must be a .joblib bundle: {artifact.name}", file=sys.stderr)
        return EXIT_CONFIG
    if not artifact.with_suffix(".json").is_file():
        print(f"Matching sidecar JSON missing: {artifact.with_suffix('.json')}", file=sys.stderr)
        return EXIT_CONFIG

    if args.verify:
        try:
            _verify_bundle(artifact)
        except Exception as exc:
            print(f"VERIFY FAILED for {sport}: {_redact(exc)}", file=sys.stderr)
            print("No upload attempted.", file=sys.stderr)
            return EXIT_CONFIG

    print(f"== UPLOAD (no training) {sport}: {artifact.name} ==", flush=True)
    try:
        _upload_artifact(artifact, sport)
    except Exception as exc:
        print(f"UPLOAD FAILED for {sport}: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        return EXIT_NETWORK
    print(f"UPLOAD {sport} OK — model registered on Render.", flush=True)
    return EXIT_OK


def _cmd_run_all(args) -> int:
    sports = [s.strip().lower() for s in (args.sports or os.environ.get("SPORTS_OVERRIDE", "")).split(",") if s.strip().lower()]
    if not sports:
        sports = list(SPORTS)
    unknown = [s for s in sports if s not in SPORTS]
    if unknown:
        print(f"Unknown sport(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_CONFIG
    # Keep canonical order so soccer always trains before basketball.
    sports = [s for s in SPORTS if s in sports]

    script = str(SCRIPT_DIR / "ec2_train_worker.py")
    results = []
    print(f"== RUN-ALL: {', '.join(sports)} (sequential, isolated child process per sport) ==", flush=True)
    # Parent process stays light: heavy app/ML modules are imported only inside
    # each child 'train' process, giving a clean OS memory boundary between sports.
    for sport in sports:
        cmd = [sys.executable, script, "train", "--sport", sport]
        if args.skip_upload:
            cmd.append("--skip-upload")
        print(f"\n=== CHILD PROCESS: {sport} (pid spawned 2 vCPU/8 GiB) — {cmd[0]} ===", flush=True)
        proc = subprocess.run(cmd, env=os.environ.copy(), check=False)
        results.append((sport, proc.returncode))
        print(f"=== {sport} CHILD EXIT CODE: {proc.returncode} ===", flush=True)

    print("\n== RUN-ALL SUMMARY ==", flush=True)
    failed = 0
    for sport, code in results:
        status = "OK" if code == EXIT_OK else f"FAILED (exit {code})"
        print(f"  {sport:<22} {status}", flush=True)
        if code != EXIT_OK:
            failed += 1
    print(f"runner exit code: {EXIT_DATA if failed else EXIT_OK}", flush=True)
    return EXIT_DATA if failed else EXIT_OK


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ec2_train_worker", description="REEDS EC2 training worker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Config + connectivity dry-run")
    p_validate.set_defaults(func=_cmd_validate)

    p_train = sub.add_parser("train", help="Train one sport (full OOF ensemble) then upload")
    p_train.add_argument("--sport", required=True)
    p_train.add_argument("--skip-upload", action="store_true", help="Train and write artifacts only; do not contact Render")
    p_train.set_defaults(func=_cmd_train)

    p_upload = sub.add_parser("upload", help="Publish an existing artifact + sidecar to GitHub and sync Render (no training)")
    p_upload.add_argument("--sport", required=True)
    p_upload.add_argument("--artifact", required=True)
    p_upload.add_argument("--verify", action="store_true",
                          help="Deserialize and structurally verify the artifact + sidecar locally before uploading")
    p_upload.set_defaults(func=_cmd_upload)

    p_run = sub.add_parser("run-all", help="Train every sport sequentially in isolated child processes")
    p_run.add_argument("--sports", help="Comma-separated subset (default/SPORTS_OVERRIDE: all)")
    p_run.add_argument("--skip-upload", action="store_true")
    p_run.set_defaults(func=_cmd_run_all)

    args = parser.parse_args(argv)

    global _MODE
    _MODE = args.command
    return args.func(args)


if __name__ == "__main__":
    sys.exit(_main())