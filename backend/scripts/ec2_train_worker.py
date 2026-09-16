"""REEDS EC2 training worker (first AWS milestone).

Primary training path: EC2 -> full OOF ensemble (train_oof.py) -> artifacts ->
unique GitHub 'models-v*' release (artifact + sidecar JSON), then registers the
artifact in the Aiven model registry with the existing activation safeguards
(register_model). No model data is ever POSTed to the API. Kaggle is the
legacy fallback worker.

Commands:
  validate    Config + connectivity dry-run. Nothing is trained or uploaded.
  train       Train ONE sport in this process with the production OOF ensemble,
              write the bundle + sidecar JSON, then publish to GitHub unless
              --skip-upload is given.
  upload      Publish an EXISTING artifact + matching sidecar to a unique
              GitHub 'models-v*' release, then register it in the model
              registry (no training). --verify deserializes and structurally
              checks the bundle first.
  run-all     Train every sport sequentially, each in its own child process so
              the OS reclaims memory completely between sports. Never concurrent.

Secrets policy: every secret (DATABASE_URL, GITHUB_TOKEN, ...) is read ONLY
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

# Make `app.*` importable regardless of how the worker is launched (systemd,
# cron, or a bare shell). Python only puts the SCRIPT's directory on sys.path,
# not the backend root, so without this the app imports fail outside environments
# that set PYTHONPATH explicitly.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_DATA = 1
EXIT_CONFIG = 2
EXIT_NETWORK = 3

# Env variable NAMES that the worker requires per command. Never their values.
# Publishing goes to GitHub releases; registration uses the local DB directly
# (no HTTP calls to any API instance).
REQUIRED_ENV = {
    "train": ("DATABASE_URL",),
    "upload": ("DATABASE_URL", "GITHUB_TOKEN"),
    "validate": ("DATABASE_URL", "GITHUB_TOKEN"),
    "run-all": (),
}

# scikit-learn must stay in 1.6.x so published bundles match the pinned backend
# runtime that deserializes them (backend/requirements.txt).
SKLEARN_MIN = (1, 6)
SKLEARN_MAX = (1, 7)

SPORTS = (
    "soccer", "basketball",
    "tennis", "american_football", "hockey", "cricket", "rugby", "baseball",
)

_STATE = {"peak_mb": 0.0}

# Canonical required bundle fields, derived from app/ml/train_oof.py:_save_bundle.
# Every artifact produced by the OOF ensemble trainer MUST contain these keys
# before it is uploaded. The list is intentionally minimal: it covers what the
# runtime needs to load and predict, not every metadata key the trainer writes.
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
    "labels",
    "training_method",
    "runtime_versions",
)

# ── Training lock ──────────────────────────────────────────────────────────
# Prevents overlapping training runs. Uses POSIX advisory flock on Linux
# (the EC2 target) which is released automatically when the process dies, so
# a killed worker can never leave a stale lock. The lock file is also removed
# on clean release. A stale file with a dead PID is detected and reclaimed.
#
# On non-Linux platforms the O_CREAT|O_EXCL create-lock is used as a fallback.
TRAINING_LOCK_FILE = Path(os.environ.get("TRAINING_LOCK_FILE", "/tmp/reeds-training.lock"))
_training_lock_fd = None
_training_lock_pid: int | None = None


def _pid_alive(pid: int) -> bool:
    """Return True if pid exists and is not a zombie we can reap."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_pid() -> int | None:
    """Read the PID recorded in the lock file, or None if unreadable."""
    try:
        if not TRAINING_LOCK_FILE.is_file():
            return None
        return int(TRAINING_LOCK_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _acquire_training_lock() -> bool:
    """Try to acquire the training lock. Returns True if acquired.

    On Linux uses flock(LOCK_EX|LOCK_NB) which is released automatically when
    the holding process exits (abnormal termination included). A stale file
    whose recorded PID is dead is reclaimed before attempting the lock.
    """
    global _training_lock_fd, _training_lock_pid

    try:
        TRAINING_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"[lock] cannot create lock directory: {exc}", file=sys.stderr)
        return False

    # Reclaim a stale lock file before attempting to lock it.
    stale_pid = _read_lock_pid()
    if stale_pid is not None and not _pid_alive(stale_pid):
        try:
            TRAINING_LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # Linux/macOS: advisory flock — released automatically on process exit.
    try:
        import fcntl  # type: ignore[import-not-found]

        _training_lock_fd = open(TRAINING_LOCK_FILE, "w")
        try:
            fcntl.flock(_training_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Lock held by a live process — expected, not an error.
            try:
                _training_lock_fd.close()
            except Exception:
                pass
            _training_lock_fd = None
            return False
        _training_lock_fd.write(str(os.getpid()))
        _training_lock_fd.flush()
        _training_lock_pid = os.getpid()
        return True
    except ImportError:
        pass
    except Exception as exc:
        # Unexpected failure — surface it rather than silently falling through
        # to a misleading "lock held" message.
        print(f"[lock] flock acquisition failed: {exc}", file=sys.stderr)
        if _training_lock_fd is not None:
            try:
                _training_lock_fd.close()
            except Exception:
                pass
            _training_lock_fd = None
        return False

    # Fallback (Windows): O_CREAT|O_EXCL create-lock.
    try:
        fd = os.open(str(TRAINING_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        _training_lock_fd = os.fdopen(fd, "w")
        _training_lock_fd.write(str(os.getpid()))
        _training_lock_fd.flush()
        _training_lock_pid = os.getpid()
        return True
    except FileExistsError:
        return False
    except Exception as exc:
        print(f"[lock] create-lock acquisition failed: {exc}", file=sys.stderr)
        return False


def _release_training_lock() -> None:
    """Release the training lock. Safe to call when no lock is held."""
    global _training_lock_fd, _training_lock_pid
    if _training_lock_fd is not None:
        try:
            import fcntl  # type: ignore[import-not-found]
            fcntl.flock(_training_lock_fd, fcntl.LOCK_UN)
        except ImportError:
            pass
        except Exception:
            pass
        finally:
            try:
                _training_lock_fd.close()
            except Exception:
                pass
            _training_lock_fd = None
    _training_lock_pid = None
    try:
        TRAINING_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


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
    """GitHub is the only publishing destination; no backend credentials needed."""
    return _missing(("GITHUB_TOKEN",))


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
    what model_bootstrap.py expects ('models-v*') when restoring missing
    artifacts on startup. The release is created first and deleted again if
    either asset upload fails so a broken release never becomes the newest
    models-v* release. Raises RuntimeError on any failure. The token is only
    ever sent as a header; it is never logged.
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


def _upload_artifact(artifact: Path, sport: str | None) -> None:
    """Publish one artifact + sidecar to a unique GitHub 'models-v*' release.

    The API instance is never contacted: model_bootstrap.py restores missing
    artifacts from these releases on startup and model_registry.py repairs
    registry paths against the local model directory. Raises RuntimeError on
    any failure so a failed publish is never mistaken for success.
    """
    artifact = artifact.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError(f"Artifact missing or empty: {artifact}")
    size_mb = artifact.stat().st_size / (1024 * 1024)
    print(f"[upload] artifact={artifact.name} size={size_mb:.1f} MiB", flush=True)
    if size_mb > 90:
        print("[upload] WARNING: artifact is large; GitHub release assets allow up to 2 GiB", flush=True)

    meta = _read_sidecar(artifact)
    target_sport = str(sport or meta.get("sport") or "").strip().lower()
    if not target_sport:
        raise RuntimeError("Could not determine sport for upload (pass --sport or provide sidecar 'sport')")
    accuracy = float(meta.get("accuracy", 0.0) or 0.0)
    sample_size = int(meta.get("sample_size", 0) or 0)
    if not 0.0 <= accuracy <= 1.0:
        raise RuntimeError(f"Invalid accuracy in sidecar: {accuracy}")
    if sample_size <= 0:
        raise RuntimeError(f"Invalid sample_size in sidecar: {sample_size}")

    sidecar = artifact.with_suffix(".json")
    tag = _publish_github_release(artifact, sidecar, target_sport)
    print(f"[upload] published GitHub release {tag}", flush=True)


def _register_published_model(artifact: Path, sport: str) -> None:
    """Register a freshly published artifact in the model registry.

    Runs on the instance that owns both the model directory and the Aiven
    database, and goes through the existing register_model() safeguards
    (minimum samples, accuracy bounds, conservative activation). The sidecar
    written next to the artifact supplies model_type/accuracy/sample_size.
    Raises RuntimeError on any failure; never POSTs anywhere.
    """
    from app.db.session import SessionLocal
    from app.services.model_registry import register_model

    artifact = artifact.resolve()
    meta = _read_sidecar(artifact)
    model_type = str(meta.get("model_type") or "uploaded")[:50]
    accuracy = float(meta.get("accuracy", 0.0) or 0.0)
    sample_size = int(meta.get("sample_size", 0) or 0)
    db = SessionLocal()
    try:
        mv = register_model(db, sport, model_type, str(artifact), accuracy, sample_size)
    finally:
        db.close()
    print(f"[registry] {sport}: registered {artifact.name} "
          f"accuracy={accuracy:.2%} sample_size={sample_size:,} active={bool(mv.is_active)}", flush=True)


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
    # Compute SHA-256 checksum for artifact integrity verification
    try:
        sha256 = _sha256_file(artifact)
        if sha256:
            payload["sha256"] = sha256
            payload["sha256_algorithm"] = "sha256"
    except Exception:
        pass
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[sidecar] wrote {sidecar.name}", flush=True)


def _sha256_file(path: Path) -> str | None:
    """Compute SHA-256 hex digest of a file in 1 MiB chunks."""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _verify_checksum(artifact: Path, sidecar: Path) -> None:
    """Verify the SHA-256 checksum stored in the sidecar matches the artifact."""
    try:
        with sidecar.open("r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except Exception:
        return  # No sidecar or invalid JSON — checksum verification skipped
    expected = str(meta.get("sha256") or "").strip().lower()
    if not expected:
        return  # No checksum recorded — skip silently
    actual = _sha256_file(artifact)
    if actual is None:
        raise RuntimeError(f"Could not compute SHA-256 for {artifact}")
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 mismatch for {artifact.name}: expected {expected[:16]}..., got {actual[:16]}..."
        )
    print(f"[checksum] SHA-256 verified for {artifact.name}", flush=True)


def _verify_bundle(artifact: Path) -> None:
    """Deserialize an artifact locally and verify its structure before upload.

    Also verifies SHA-256 checksum if present in the sidecar. Raises RuntimeError
    on any failure. Never modifies the artifact. Callers requesting verification
    must let this pass before publishing.
    """
    artifact = artifact.resolve()
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError(f"Artifact missing or empty: {artifact}")
    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file():
        raise RuntimeError(f"Sidecar metadata missing: {sidecar}")

    # SHA-256 checksum verification (if recorded in sidecar)
    _verify_checksum(artifact, sidecar)

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
    print(f"GITHUB_REPO={os.environ.get('GITHUB_REPO', 'zagzy8776/REEDS')}")

    try:
        import sklearn
        version = tuple(int(part) for part in sklearn.__version__.split(".")[:2])
    except Exception as exc:
        print(f"Could not import scikit-learn: {_redact(exc)}", file=sys.stderr)
        return EXIT_CONFIG
    locked = SKLEARN_MIN <= version < SKLEARN_MAX
    print(f"scikit-learn {sklearn.__version__} (lock range {'1.6.x' if locked else 'OUT OF RANGE'})")
    if not locked:
        print("scikit-learn outside the 1.6.x lock pinned in backend/requirements.txt.", file=sys.stderr)
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

    print("-- GitHub releases connectivity --", flush=True)
    import requests
    try:
        repo = os.environ.get("GITHUB_REPO", "zagzy8776/REEDS").strip() or "zagzy8776/REEDS"
        response = requests.get(
            f"https://api.github.com/repos/{repo}/releases?per_page=30",
            headers=_github_headers(),
            timeout=20,
        )
        print(f"GET /repos/{repo}/releases -> HTTP {response.status_code}")
        if response.status_code != 200:
            print("GitHub releases check FAILED (check GITHUB_TOKEN and GITHUB_REPO).", file=sys.stderr)
            return EXIT_NETWORK
        releases = [
            release for release in response.json()
            if str(release.get("tag_name", "")).startswith("models-v")
        ]
        if releases:
            print(f"  newest models-v* release: {releases[0].get('tag_name')}")
        else:
            print("  no models-v* releases yet (expected on first publish)")
    except Exception as exc:
        print(f"GitHub releases check FAILED: {_redact(exc)}", file=sys.stderr)
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

    # ── Training lock: prevent overlapping runs ──────────────────────────
    if not getattr(args, "force", False):
        if not _acquire_training_lock():
            print(
                "Training lock is held by another process. "
                "Use --force to override (NOT recommended).",
                file=sys.stderr,
            )
            return EXIT_CONFIG
    else:
        _acquire_training_lock()

    min_rows = int(os.environ.get("MIN_TRAINING_ROWS", "200"))

    print(f"== TRAIN {sport} (full OOF ensemble, process pid {os.getpid()}) ==", flush=True)
    model_dir = os.environ.get("MODEL_DIR") or str(BACKEND_DIR / "data" / "models")
    print(f"MODEL_DIR={Path(model_dir).resolve()}", flush=True)
    _log_rss("before db load")

    try:
        return _train_sport(args, sport, min_rows)
    finally:
        _release_training_lock()


def _train_sport(args, sport: str, min_rows: int) -> int:
    """Core training logic for a single sport (lock already held)."""

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

    try:
        _register_published_model(artifact, sport)
    except Exception as exc:
        print(f"REGISTRATION FAILED for {sport}: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        print(f"Artifact published to GitHub but not registered; preserved locally: {artifact} — re-register with: "
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
    try:
        _register_published_model(artifact, sport)
    except Exception as exc:
        print(f"REGISTRATION FAILED for {sport}: {type(exc).__name__}: {_redact(exc)}", file=sys.stderr)
        print(f"Artifact is on GitHub releases but not registered. Re-run: "
              f"python {SCRIPT_DIR / 'ec2_train_worker.py'} upload --sport {sport} --artifact {artifact}", file=sys.stderr)
        return EXIT_NETWORK
    print(f"UPLOAD {sport} OK — published to GitHub releases and registered.", flush=True)
    return EXIT_OK


def _cmd_run_all(args) -> int:
    """Train every sport sequentially in isolated child processes.

    The parent does NOT hold the training lock. Each child process acquires
    the lock itself (via _cmd_train) before training and releases it on
    completion, so:
      - overlapping runs are still prevented (the lock is held during each
        sport's training + upload),
      - a killed parent can never leave a stale lock, and
      - the lock is never held across the whole multi-sport run, which would
        make every child immediately exit with "lock held".
    """
    return _run_all_sports(args)


def _run_all_sports(args) -> int:
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
    # Individual sport failures do NOT abort the run — each sport is independent.
    for sport in sports:
        cmd = [sys.executable, script, "train", "--sport", sport]
        if args.skip_upload:
            cmd.append("--skip-upload")
        print(f"\n=== CHILD PROCESS: {sport} (pid spawned 2 vCPU/8 GiB) — {cmd[0]} ===", flush=True)
        proc = subprocess.run(cmd, env=os.environ.copy(), check=False, capture_output=True, text=True)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if stdout:
            print(stdout, flush=True)
        if stderr:
            print(stderr, file=sys.stderr, flush=True)
        results.append((sport, proc.returncode, stdout, stderr))
        print(f"=== {sport} CHILD EXIT CODE: {proc.returncode} ===", flush=True)

    print("\n== RUN-ALL SUMMARY ==", flush=True)
    failed = 0
    for sport, code, stdout, _stderr in results:
        status = "OK" if code == EXIT_OK else f"FAILED (exit {code})"
        print(f"  {sport:<22} {status}", flush=True)
        if code != EXIT_OK:
            failed += 1
            continue
        # Extract the TRAIN OK line for the structured report.
        for line in stdout.splitlines():
            if line.startswith(f"TRAIN {sport} OK:"):
                print(f"    {line.strip()}", flush=True)
                break
        # Extract artifact line.
        for line in stdout.splitlines():
            if line.startswith("[artifact]"):
                print(f"    {line.strip()}", flush=True)
                break
        # Extract upload line.
        for line in stdout.splitlines():
            if "published GitHub release" in line:
                print(f"    {line.strip()}", flush=True)
                break
    print(f"runner exit code: {EXIT_DATA if failed else EXIT_OK}", flush=True)
    return EXIT_DATA if failed else EXIT_OK


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ec2_train_worker", description="REEDS EC2 training worker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Config + connectivity dry-run")
    p_validate.set_defaults(func=_cmd_validate)

    p_train = sub.add_parser("train", help="Train one sport (full OOF ensemble) then publish to GitHub")
    p_train.add_argument("--sport", required=True)
    p_train.add_argument("--skip-upload", action="store_true", help="Train and write artifacts only; do not publish")
    p_train.add_argument("--force", action="store_true", help="Override training lock (NOT recommended)")
    p_train.set_defaults(func=_cmd_train)

    p_upload = sub.add_parser("upload", help="Publish an existing artifact + sidecar to GitHub releases (no training)")
    p_upload.add_argument("--sport", required=True)
    p_upload.add_argument("--artifact", required=True)
    p_upload.add_argument("--verify", action="store_true",
                          help="Deserialize and structurally verify the artifact + sidecar locally before uploading")
    p_upload.set_defaults(func=_cmd_upload)

    p_run = sub.add_parser("run-all", help="Train every sport sequentially in isolated child processes")
    p_run.add_argument("--sports", help="Comma-separated subset (default/SPORTS_OVERRIDE: all)")
    p_run.add_argument("--skip-upload", action="store_true")
    p_run.add_argument("--force", action="store_true", help="Override training lock (NOT recommended)")
    p_run.set_defaults(func=_cmd_run_all)

    args = parser.parse_args(argv)

    global _MODE
    _MODE = args.command
    return args.func(args)


if __name__ == "__main__":
    sys.exit(_main())