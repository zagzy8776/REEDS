"""LOYAL EDGE — Hugging Face training worker.

The Space is the ML worker. Render serves the public API; Neon is the source of
truth. The worker refreshes recent provider history through Render, trains the
available sport models from completed Neon data, publishes artifacts to Render,
and polls Render for retraining signals.
"""

import hashlib
import os
import sys
import time
import subprocess
import threading
from pathlib import Path

import gradio as gr
import requests


DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
RENDER_URL = os.environ.get("RENDER_URL", "https://reeds-phj1.onrender.com").rstrip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "zagzy8776/REEDS")
PROVIDER_HISTORY_DAYS = max(1, min(int(os.environ.get("PROVIDER_HISTORY_DAYS", "3") or 3), 7))

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["MODEL_DIR"] = "/tmp/models"
os.environ["MIN_TRAINING_ROWS"] = "200"
os.environ["APP_ENV"] = "production"
os.makedirs("/tmp/models", exist_ok=True)

TRAINABLE_SPORTS = [
    "soccer",
    "basketball",
    "tennis",
    "american_football",
    "hockey",
    "cricket",
    "rugby",
    "baseball",
]

_log_lines: list[str] = []
_log_lock = threading.Lock()
_train_lock = threading.Lock()
_poll_stop = threading.Event()
_poll_thread: threading.Thread | None = None
_train_thread: threading.Thread | None = None
_train_state = {"running": False, "started_at": None, "finished_at": None, "last_result": ""}


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with _log_lock:
        _log_lines.append(line)
        if len(_log_lines) > 800:
            _log_lines[:] = _log_lines[-500:]
    print(line, flush=True)


def _get_log() -> str:
    with _log_lock:
        return "\n".join(_log_lines[-300:])


REPO_DIR = "/home/user/app/REEDS"
BACKEND_DIR = os.path.join(REPO_DIR, "backend")


def _ensure_repo(force: bool = False) -> bool:
    """Clone/reset to origin/main so the worker never trains stale code."""
    try:
        if not os.path.exists(REPO_DIR) or force:
            if os.path.exists(REPO_DIR):
                subprocess.run(["rm", "-rf", REPO_DIR], cwd="/home/user", timeout=30)
            _log("📥 Pulling latest REEDS main...")
            result = subprocess.run(
                ["git", "clone", "--depth=1", "https://github.com/zagzy8776/REEDS.git", REPO_DIR],
                cwd="/home/user", capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                _log(f"❌ Clone failed: {result.stderr[-500:]}")
                return False
        else:
            fetch = subprocess.run(
                ["git", "-C", REPO_DIR, "fetch", "origin", "main"],
                capture_output=True, text=True, timeout=60,
            )
            if fetch.returncode != 0:
                _log(f"⚠️ Git fetch failed: {fetch.stderr[-300:]}")
            reset = subprocess.run(
                ["git", "-C", REPO_DIR, "reset", "--hard", "origin/main"],
                capture_output=True, text=True, timeout=60,
            )
            if reset.returncode != 0:
                _log(f"❌ Git reset failed: {reset.stderr[-300:]}")
                return False

        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        if BACKEND_DIR not in sys.path:
            sys.path.insert(0, BACKEND_DIR)
        _log("✅ REEDS main is current")
        return True
    except Exception as exc:
        _log(f"❌ Repo setup failed: {exc}")
        return False


_ensure_repo()


def _get_db():
    from app.db.session import SessionLocal, init_db
    init_db()
    return SessionLocal()


def _load_data(db):
    from app.services.predictions import dataframe_from_db
    data = dataframe_from_db(db, max_age_days=None)
    if "sport" in data.columns:
        data["sport"] = data["sport"].astype(str).str.strip().str.lower()
    return data


def _render_headers() -> dict:
    return {"x-admin-key": ADMIN_KEY.strip()} if ADMIN_KEY.strip() else {}


def _wake_headers() -> dict:
    value = CRON_SECRET.strip()
    return {"X-Cron-Secret": value} if value else {}


def _cron_diagnostic(value: str) -> dict:
    value = value.strip()
    return {
        "configured": bool(value),
        "length": len(value),
        "sha256_prefix": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else "",
    }


def _sync_provider_history() -> tuple[bool, str]:
    if not ADMIN_KEY:
        return False, "ADMIN_API_KEY secret is not configured"
    try:
        response = requests.post(
            f"{RENDER_URL}/api/admin/ml/sync-provider-history",
            headers=_render_headers(),
            params={"days_back": PROVIDER_HISTORY_DAYS},
            timeout=180,
        )
        if not response.ok:
            return False, f"Render provider history sync HTTP {response.status_code}: {response.text[:300]}"
        data = response.json()
        providers = data.get("providers", {})
        completed = int(data.get("completed_rows", 0) or 0)
        _log(
            f"📚 Provider history sync: {completed:,} completed rows "
            f"over {PROVIDER_HISTORY_DAYS}d | providers={providers}"
        )
        if data.get("errors"):
            _log(f"⚠️ Provider history sync errors: {data['errors']}")
        return True, response.text[:1000]
    except Exception as exc:
        return False, f"provider history sync error: {exc}"


def action_force_pull():
    ok = _ensure_repo(force=True)
    message = "✅ Force-pull complete" if ok else "❌ Force-pull failed"
    return message, _get_log()


def action_check_db():
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret is missing", _get_log()
    if not _ensure_repo():
        return "❌ Could not load current REEDS code", _get_log()
    db = None
    try:
        db = _get_db()
        data = _load_data(db)
        if data.empty:
            return "📁 Neon is reachable but has no fixture rows", _get_log()
        lines = [f"✅ Neon connected | {len(data):,} total rows"]
        for sport, group in data.groupby("sport"):
            completed = int((group["home_score"].notna() & group["away_score"].notna()).sum())
            lines.append(f"  {sport:<20} {completed:>7,} completed / {len(group):>7,} total")
        return "\n".join(lines), _get_log()
    except Exception as exc:
        return f"❌ DB error: {exc}", _get_log()
    finally:
        if db is not None:
            db.close()


def action_sync_provider_history():
    if not DATABASE_URL or not ADMIN_KEY:
        return "❌ Add DATABASE_URL and ADMIN_API_KEY to Space Settings first.", _get_log()
    ok, detail = _sync_provider_history()
    message = "✅ Provider history synchronized into Neon" if ok else f"❌ {detail}"
    return message, _get_log()


def action_ingest(max_leagues: int):
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret is missing", _get_log()
    if not _ensure_repo():
        return "❌ Could not load current REEDS code", _get_log()
    db = None
    try:
        db = _get_db()
        from app.scraper.free_data import (
            ingest_football_data_co_uk, ingest_openfootball,
            ingest_tennis_atp, ingest_tennis_wta, ingest_tennis_data_co_uk,
            ingest_nba_github, ingest_nfl_spreadspoke, ingest_nhl_api,
            ingest_ipl_github, ingest_rugby_openfootball, ingest_mlb_retrosheet,
        )
        jobs = [
            ("soccer football-data", ingest_football_data_co_uk, (None, None, int(max_leagues))),
            ("soccer openfootball", ingest_openfootball, ()),
            ("tennis ATP", ingest_tennis_atp, ()),
            ("tennis WTA", ingest_tennis_wta, ()),
            ("tennis data.co.uk", ingest_tennis_data_co_uk, ()),
            ("basketball NBA", ingest_nba_github, ()),
            ("american football NFL", ingest_nfl_spreadspoke, ()),
            ("hockey NHL", ingest_nhl_api, ()),
            ("cricket IPL", ingest_ipl_github, ()),
            ("rugby", ingest_rugby_openfootball, ()),
            ("baseball MLB", ingest_mlb_retrosheet, ()),
        ]
        results = []
        for name, fn, args in jobs:
            try:
                _log(f"📥 {name}...")
                result = fn(db, *args)
                total = result.get("total", 0) if isinstance(result, dict) else int(result or 0)
                results.append(f"{name}: {total:,}")
                _log(f"  {results[-1]}")
            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                results.append(f"{name}: ERROR {exc}")
                _log(f"  ❌ {results[-1]}")
        return "\n".join(["✅ Free historical ingestion finished", *results]), _get_log()
    except Exception as exc:
        return f"❌ Free-data ingestion failed: {exc}", _get_log()
    finally:
        if db is not None:
            db.close()


def _upload_to_render(path: str, sport: str, model_type: str, accuracy: float, sample_size: int) -> tuple[bool, str]:
    """Upload a model directly; use an atomic GitHub-release sync fallback."""
    if not ADMIN_KEY:
        return False, "ADMIN_API_KEY secret is not configured in the Space"
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
    except OSError as exc:
        return False, f"file check failed: {exc}"

    safe_model_type = str(model_type)[:50]
    if size_mb <= 50:
        for attempt in range(2):
            try:
                with open(path, "rb") as handle:
                    response = requests.post(
                        f"{RENDER_URL}/api/admin/upload-model",
                        headers=_render_headers(),
                        files={"model": (Path(path).name, handle, "application/octet-stream")},
                        data={
                            "sport": sport,
                            "model_type": safe_model_type,
                            "accuracy": str(accuracy),
                            "sample_size": str(sample_size),
                        },
                        timeout=120,
                    )
                if response.ok:
                    return True, response.text[:300]
                _log(f"⚠️ {sport} direct upload HTTP {response.status_code} (attempt {attempt + 1}/2)")
            except Exception as exc:
                _log(f"⚠️ {sport} direct upload error: {exc}")
            time.sleep(3)

    if not GITHUB_TOKEN:
        return False, "direct upload failed and GITHUB_TOKEN is not configured"

    try:
        tag = f"models-v{time.strftime('%Y%m%d%H%M%S')}-{sport}"
        release = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
            json={
                "tag_name": tag,
                "name": f"LOYAL EDGE model: {sport}",
                "body": f"Auto-trained {sport}; accuracy={accuracy:.4f}; rows={sample_size}",
                "draft": False,
                "prerelease": False,
            },
            timeout=30,
        )
        if not release.ok:
            return False, f"release creation failed: HTTP {release.status_code}"

        upload_url = release.json()["upload_url"].replace("{?name,label}", "")
        with open(path, "rb") as handle:
            asset = requests.post(
                f"{upload_url}?name={Path(path).name}",
                headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/octet-stream"},
                data=handle,
                timeout=120,
            )
        if not asset.ok:
            return False, f"release asset failed: HTTP {asset.status_code}"

        pull = requests.post(
            f"{RENDER_URL}/api/admin/sync-models-safe",
            headers=_render_headers(),
            json={},
            timeout=120,
        )
        if pull.ok:
            return True, f"GitHub Release fallback OK ({tag})"
        return False, f"release uploaded but atomic Render sync returned HTTP {pull.status_code}"
    except Exception as exc:
        return False, f"release fallback error: {exc}"


def _train_all_sports() -> None:
    global _train_state
    logs: list[str] = []
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with _train_lock:
        _train_state.update({"running": True, "started_at": started_at, "finished_at": None, "last_result": ""})
    try:
        if not DATABASE_URL or not ADMIN_KEY:
            raise RuntimeError("DATABASE_URL and ADMIN_API_KEY are required")
        if not _ensure_repo():
            raise RuntimeError("Could not load current REEDS code")

        ok, detail = _sync_provider_history()
        if not ok:
            _log(f"⚠️ Continuing with existing Neon history: {detail}")

        db = _get_db()
        try:
            data = _load_data(db)
            if data.empty:
                raise RuntimeError("Neon has no training data")
            completed_mask = data["home_score"].notna() & data["away_score"].notna()
            data = data[completed_mask].copy()
            _log(f"⚡ ML training snapshot: {len(data):,} completed rows across Neon")
            sport_counts = data.groupby("sport").size().to_dict()
            _log("📊 Completed rows by sport: " + ", ".join(f"{k}={v:,}" for k, v in sorted(sport_counts.items())))
        finally:
            db.close()

        from app.ml.train import train_soccer_model, train_basketball_model, train_generic_sport_model
        from app.services.model_registry import register_model

        trainers = {
            "soccer": train_soccer_model,
            "basketball": train_basketball_model,
            "tennis": lambda frame: train_generic_sport_model(frame, "tennis"),
            "american_football": lambda frame: train_generic_sport_model(frame, "american_football"),
            "hockey": lambda frame: train_generic_sport_model(frame, "hockey"),
            "cricket": lambda frame: train_generic_sport_model(frame, "cricket"),
            "rugby": lambda frame: train_generic_sport_model(frame, "rugby"),
            "baseball": lambda frame: train_generic_sport_model(frame, "baseball"),
        }

        for sport in TRAINABLE_SPORTS:
            completed = int((data["sport"] == sport).sum())
            if completed < 200:
                line = f"⏩ {sport.upper()}: skipped — only {completed:,} completed rows (need 200)"
                _log(line); logs.append(line); continue
            try:
                frame = data[data["sport"] == sport].copy()
                _log(f"🏋️ Training {sport} on {len(frame):,} completed rows...")
                started = time.time()
                result = trainers[sport](frame)
                elapsed = int(time.time() - started)

                db = _get_db()
                try:
                    register_model(
                        db, sport, str(result["model_type"])[:50], result["path"],
                        result["accuracy"], result["sample_size"]
                    )
                    db.commit()
                finally:
                    db.close()

                ok, detail = _upload_to_render(
                    result["path"], sport, result["model_type"], result["accuracy"], result["sample_size"]
                )
                line = (
                    f"{'✅' if ok else '⚠️'} {sport.upper()}: accuracy={result['accuracy']:.1%} "
                    f"rows={result['sample_size']:,} time={elapsed}s upload={'OK' if ok else detail[:100]}"
                )
                _log(line); logs.append(line)
            except Exception as exc:
                line = f"❌ {sport.upper()}: {exc}"
                _log(line); logs.append(line)

        for endpoint in ("/api/admin/predict", "/api/admin/backfill-odds", "/api/admin/clear-train-flag"):
            try:
                response = requests.post(f"{RENDER_URL}{endpoint}", headers=_render_headers(), timeout=90)
                _log(f"Render {endpoint}: HTTP {response.status_code}")
            except Exception as exc:
                _log(f"Render {endpoint}: {exc}")
        logs.append("⚡ Render synchronization requested")
    except Exception as exc:
        logs.append(f"❌ Training job failed: {exc}")
        _log(logs[-1])
    finally:
        with _train_lock:
            _train_state.update({"running": False, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"), "last_result": "\n".join(logs)})


def action_train():
    global _train_thread
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret is missing", _get_log()
    if not ADMIN_KEY:
        return "❌ ADMIN_API_KEY secret is missing", _get_log()
    if _train_thread and _train_thread.is_alive():
        return "⏳ Training is already running. Use Refresh Log to watch it.", _get_log()
    if not _train_lock.acquire(blocking=False):
        return "⏳ Training is already running", _get_log()
    _train_lock.release()
    _train_thread = threading.Thread(target=_train_all_sports, name="ml-training", daemon=True)
    _train_thread.start()
    return "🚀 Training job started in background. Provider history sync runs first; use Refresh Log to watch each stage.", _get_log()


def action_model_status():
    try:
        response = requests.get(f"{RENDER_URL}/api/stats/backtest", timeout=20)
        if not response.ok:
            return f"❌ Render HTTP {response.status_code}", _get_log()
        models = response.json().get("models", [])
        lines = [f"HF training job: {'RUNNING' if _train_state['running'] else 'IDLE'}"]
        if _train_state.get("started_at"):
            lines.append(f"Started: {_train_state['started_at']}")
        if _train_state.get("finished_at"):
            lines.append(f"Finished: {_train_state['finished_at']}")
        lines.append("")
        if not models:
            lines.append("No active models on Render yet.")
            return "\n".join(lines), _get_log()
        lines.append("SPORT                  TYPE                           ROWS     ACC  STATUS")
        lines.append("-" * 82)
        for model in models:
            status = "🟢 ACTIVE" if model.get("active") else "🔵"
            lines.append(
                f"{model.get('sport',''):<22} {str(model.get('type',''))[:30]:<30} "
                f"{int(model.get('sample_size',0)):>7,}  {float(model.get('accuracy',0))*100:>5.1f}%  {status}"
            )
        return "\n".join(lines), _get_log()
    except Exception as exc:
        return f"❌ {exc}", _get_log()


def action_wake_render():
    local = _cron_diagnostic(CRON_SECRET)
    _log(
        "🔐 HF cron credential: "
        f"configured={local['configured']} length={local['length']} sha256={local['sha256_prefix']}"
    )
    try:
        health = requests.get(f"{RENDER_URL}/api/health", timeout=20)
        diagnostic = None
        if ADMIN_KEY:
            try:
                diag_response = requests.get(
                    f"{RENDER_URL}/api/admin/cron-diagnostics",
                    headers=_render_headers(),
                    timeout=20,
                )
                if diag_response.ok:
                    diagnostic = diag_response.json()
                    _log(
                        "🔎 Render cron credential: "
                        f"configured={diagnostic.get('configured')} "
                        f"length={diagnostic.get('length')} "
                        f"sha256={diagnostic.get('sha256_prefix')}"
                    )
            except Exception as exc:
                _log(f"⚠️ Render credential diagnostic unavailable: {exc}")

        response = requests.get(f"{RENDER_URL}/api/wake", headers=_wake_headers(), timeout=60)
        if response.ok:
            data = response.json()
            message = (
                "✅ HF → Render connection authenticated\n"
                f"health: HTTP {health.status_code}\n"
                f"wake: HTTP {response.status_code}\n"
                f"coverage refresh queued: {data.get('coverage_refresh_queued', False)}\n"
                f"existing future fixtures: {data.get('existing_fixtures', 0)}"
            )
        else:
            mismatch = "unknown"
            if diagnostic:
                mismatch = "MATCH" if (
                    diagnostic.get("configured") == local.get("configured")
                    and diagnostic.get("length") == local.get("length")
                    and diagnostic.get("sha256_prefix") == local.get("sha256_prefix")
                ) else "MISMATCH"
            message = (
                f"⚠️ HF reached Render but wake returned HTTP {response.status_code}\n"
                f"cron credential comparison: {mismatch}\n"
                f"HF configured={local.get('configured')} length={local.get('length')}\n"
                f"Render configured={diagnostic.get('configured') if diagnostic else 'unknown'} "
                f"length={diagnostic.get('length') if diagnostic else 'unknown'}"
            )
    except Exception as exc:
        message = f"❌ HF → Render connection failed: {exc}"
    _log(message)
    return message, _get_log()


def _poll_loop(interval: int = 60) -> None:
    _log(f"🔄 Auto-poll started ({interval}s)")
    while not _poll_stop.is_set():
        try:
            if not ADMIN_KEY:
                _log("⏸️ Auto-poll disabled: ADMIN_API_KEY is missing")
            else:
                response = requests.get(
                    f"{RENDER_URL}/api/admin/job-status",
                    headers=_render_headers(), timeout=20,
                )
                if response.ok:
                    status = response.json()
                    _log(
                        f"Poll: model={int(status.get('current_model_rows',0)):,} "
                        f"db={int(status.get('db_soccer_rows',0)):,} "
                        f"trigger={status.get('trigger_train', False)} ({status.get('reason','none')})"
                    )
                    if status.get("trigger_train") and not (_train_thread and _train_thread.is_alive()):
                        _log("🏋️ Retrain trigger detected")
                        action_train()
                else:
                    _log(f"⚠️ Poll HTTP {response.status_code}")
        except Exception as exc:
            _log(f"⚠️ Poll error: {exc}")
        _poll_stop.wait(interval)


def action_toggle_poll():
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        _poll_stop.set()
        return "⏹️ Auto-poll stopping...", _get_log()
    if not DATABASE_URL or not ADMIN_KEY:
        return "❌ Add DATABASE_URL and ADMIN_API_KEY to Space Settings first.", _get_log()
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(60,), daemon=True)
    _poll_thread.start()
    return "✅ Auto-poll enabled (60s)", _get_log()


with gr.Blocks(title="LOYAL EDGE Trainer", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        """# 🏆 LOYAL EDGE — AI Engine
Trains production models from completed Neon history, including recent history synchronized from the real API providers through Render, and publishes them to Render.

**Required Space secrets:** `DATABASE_URL` · `ADMIN_API_KEY` · `RENDER_URL` · `CRON_SECRET` · optional `GITHUB_TOKEN`.
"""
    )

    with gr.Row():
        btn_pull = gr.Button("🔄 Force Pull Latest Code", variant="stop", scale=2)
        btn_db = gr.Button("🔍 Check DB", variant="secondary")
        btn_status = gr.Button("📈 Model Status", variant="secondary")
        btn_wake = gr.Button("🌐 Wake Render", variant="secondary")
        btn_log = gr.Button("🔃 Refresh Log", variant="secondary")

    gr.Markdown("### 📥 Step 1 — Sync provider history + free historical datasets")
    with gr.Row():
        btn_provider_history = gr.Button(f"🌐 Sync API Provider History ({PROVIDER_HISTORY_DAYS}d)", variant="secondary")
        ingest_slider = gr.Slider(1, 21, value=21, step=1, label="Max soccer leagues (free datasets)", scale=2)
        btn_ingest = gr.Button("⬇️ Ingest Free Historical Data", variant="primary")

    gr.Markdown("### 🏋️ Step 2 — Train and publish production models")
    with gr.Row():
        btn_train = gr.Button("🚀 Train All + Upload to Render", variant="primary", scale=2)
        btn_poll = gr.Button("🔄 Toggle Auto-Poll (60s)", variant="stop", scale=1)

    result_box = gr.Textbox(label="Output", lines=18, interactive=False)
    log_box = gr.Textbox(label="Live log", lines=10, interactive=False)

    btn_pull.click(action_force_pull, outputs=[result_box, log_box])
    btn_db.click(action_check_db, outputs=[result_box, log_box])
    btn_status.click(action_model_status, outputs=[result_box, log_box])
    btn_wake.click(action_wake_render, outputs=[result_box, log_box])
    btn_log.click(lambda: _get_log(), outputs=[log_box])
    btn_provider_history.click(action_sync_provider_history, outputs=[result_box, log_box])
    btn_ingest.click(action_ingest, inputs=[ingest_slider], outputs=[result_box, log_box])
    btn_train.click(action_train, outputs=[result_box, log_box])
    btn_poll.click(action_toggle_poll, outputs=[result_box, log_box])

    gr.Markdown("---\n**Workflow:** Force Pull → Check DB → Sync API Provider History → Ingest Free History → Background Training → Model Status → Auto-Poll")


if DATABASE_URL and ADMIN_KEY:
    _log("Space booted — starting auto-poll (60s)...")
    _poll_thread = threading.Thread(target=_poll_loop, args=(60,), daemon=True)
    _poll_thread.start()
elif not DATABASE_URL:
    _log("Space booted — DATABASE_URL is missing")
elif not ADMIN_KEY:
    _log("Space booted — ADMIN_API_KEY is missing")


if __name__ == "__main__":
    demo.launch(share=False)
