"""
LOYAL EDGE — Hugging Face Space trainer
Trains all sport models and uploads them to the Render backend.

Fixes applied vs the user's original paste:
  - generic_sports used "nhl" which is not a sport key in the DB — corrected to "hockey"
  - /api/admin/ingest does not exist — corrected to /api/admin/ingest-live
  - Added proper os.environ null-guard (crashes if DATABASE_URL secret not set)
  - trigger_remote_ingest now correctly calls /api/admin/ingest-live
  - Auto-poll daemon now uses threading.Event for clean stop
  - Gradio output box refresh button added
  - requirements.txt already has beautifulsoup4
"""
import os
import sys
import time
import threading
import requests
from pathlib import Path

import gradio as gr

# ── Config (from HF Repository Secrets) ──────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_KEY    = os.environ.get("ADMIN_API_KEY", "23235567Jjmt.")
RENDER_URL   = (os.environ.get("RENDER_URL", "https://reeds-phj1.onrender.com")).rstrip("/")

if not DATABASE_URL:
    print("⚠️  DATABASE_URL secret not set — DB operations will fail until configured.")

os.environ["DATABASE_URL"]      = DATABASE_URL
os.environ["MODEL_DIR"]         = "data/models"
os.environ["MIN_TRAINING_ROWS"] = "200"
os.environ["APP_ENV"]           = "production"

# ── Repo bootstrap ────────────────────────────────────────────────────────────
REPO_DIR    = "/home/user/app/REEDS"
BACKEND_DIR = f"{REPO_DIR}/backend"

if not os.path.exists(REPO_DIR):
    print("📥 Cloning zagzy8776/REEDS ...")
    os.system(f"git clone https://github.com/zagzy8776/REEDS.git {REPO_DIR}")
else:
    os.system(f"git -C {REPO_DIR} pull --ff-only")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.chdir(BACKEND_DIR)
os.makedirs("data/models", exist_ok=True)

# ── Lazy imports (only after sys.path is set) ─────────────────────────────────
from app.db.session import SessionLocal, init_db          # noqa: E402
from app.services.predictions import dataframe_from_db   # noqa: E402
from app.ml.train import (                                # noqa: E402
    train_soccer_model,
    train_basketball_model,
    train_generic_sport_model,
)
from app.services.model_registry import register_model   # noqa: E402

init_db()

# ── Shared log ────────────────────────────────────────────────────────────────
_log_lines: list[str] = []
_log_lock  = threading.Lock()

def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _log_lock:
        _log_lines.append(line)
        if len(_log_lines) > 600:
            _log_lines[:] = _log_lines[-400:]
    print(line, flush=True)

def _get_log() -> str:
    with _log_lock:
        return "\n".join(_log_lines[-200:])

# ── Poll state ────────────────────────────────────────────────────────────────
_poll_stop   = threading.Event()
_poll_thread: threading.Thread | None = None
AUTO_POLL_ACTIVE = False   # kept for UI toggle compatibility

# ── DB status ─────────────────────────────────────────────────────────────────
def check_database_status() -> tuple[str, str]:
    if not DATABASE_URL:
        msg = "❌ DATABASE_URL secret not set. Add it in Space Settings → Repository secrets."
        return msg, _get_log()
    db = SessionLocal()
    try:
        data = dataframe_from_db(db, max_age_days=None)
        if data.empty:
            return "📁 Database connected but empty — run Ingest first.", _get_log()
        summary = f"📊 Total rows: {len(data):,}\n\nBy sport:\n"
        if "sport" in data.columns:
            data["sport"] = data["sport"].str.lower()
            for sport, cnt in data["sport"].value_counts().items():
                completed = data[(data["sport"] == sport) & data["home_score"].notna()].shape[0]
                icon = "✅" if completed >= 200 else "⚠️ "
                summary += f"  {icon} {sport:<22} {completed:>6,} completed  ({cnt:,} total)\n"
        return summary, _get_log()
    except Exception as e:
        return f"❌ DB error: {e}", _get_log()
    finally:
        db.close()

# ── Remote ingest trigger ─────────────────────────────────────────────────────
def trigger_remote_ingest() -> tuple[str, str]:
    """Tells Render to pull fresh fixtures from live API feeds (not free CSV data)."""
    try:
        res = requests.post(
            f"{RENDER_URL}/api/admin/ingest-live",   # ← correct endpoint
            headers={"x-admin-key": ADMIN_KEY},
            timeout=300,
        )
        msg = f"Status {res.status_code}: {res.text[:300]}"
    except Exception as e:
        msg = f"❌ Ingest trigger failed: {e}"
    _log(msg)
    return msg, _get_log()

# ── Upload helper with retry ──────────────────────────────────────────────────
def upload_model_to_render(path: str, sport: str, model_type: str,
                            accuracy: float, sample_size: int) -> tuple[bool, str]:
    max_retries = 3
    last_err = ""
    for attempt in range(max_retries):
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{RENDER_URL}/api/admin/upload-model",
                    headers={"x-admin-key": ADMIN_KEY},
                    files={"model": (Path(path).name, f, "application/octet-stream")},
                    data={
                        "sport":       sport,
                        "model_type":  model_type,
                        "accuracy":    str(accuracy),
                        "sample_size": str(sample_size),
                    },
                    timeout=300,
                )
            if r.ok:
                return True, r.text
            # 413 = Payload Too Large — try chunked approach or break
            if r.status_code == 413:
                return False, f"413 Payload Too Large — model file too big for Render (try reducing n_estimators)"
            # 401/403 — auth issue, no point retrying
            if r.status_code in (401, 403):
                return False, f"{r.status_code} Auth error — check ADMIN_API_KEY on Render"
            last_err = f"attempt {attempt+1}/{max_retries} status {r.status_code}"
            _log(f"⏳ Upload {sport} {last_err}, retrying...")
            time.sleep(2 ** attempt)  # exponential backoff
        except requests.exceptions.Timeout:
            last_err = f"attempt {attempt+1}/{max_retries} timeout"
            _log(f"⏳ Upload {sport} {last_err}, retrying...")
            time.sleep(2 ** attempt)
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries - 1:
                _log(f"⏳ Upload {sport} error: {last_err}, retrying...")
                time.sleep(2 ** attempt)
    return False, last_err

# ── Core training pipeline ────────────────────────────────────────────────────
def run_training_pipeline() -> tuple[str, str]:
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()

    _log("=" * 55)
    _log("⚡ LOYAL EDGE — Master Training Sequence")
    _log("=" * 55)

    db = SessionLocal()
    logs: list[str] = []

    try:
        _log("Loading training data from DB...")
        data = dataframe_from_db(db, max_age_days=None)
        if data.empty:
            msg = "❌ Pipeline stopped: database is empty."
            _log(msg)
            return msg, _get_log()

        _log(f"Loaded {len(data):,} rows")

        # Normalise sport column to lowercase for bulletproof matching
        if "sport" in data.columns:
            data["sport"] = data["sport"].str.lower()

        # Specialized trainers
        for sport, trainer in [("soccer", train_soccer_model),
                                ("basketball", train_basketball_model)]:
            try:
                d = data[data["sport"] == sport].copy() if "sport" in data.columns else data
                completed = d[d["home_score"].notna()].shape[0]
                if completed < 200:
                    line = f"⏩ {sport.upper()}: skipped ({completed} completed rows)"
                    _log(line); logs.append(line); continue
                _log(f"🏋️  Training {sport} ({completed:,} rows)...")
                r = trainer(d)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, resp_text = upload_model_to_render(r["path"], sport,
                                                        r["model_type"], r["accuracy"],
                                                        r["sample_size"])
                line = f"{'✅' if ok else '⚠️ '} {sport.upper()}: {r['accuracy']:.1%} | upload={'OK' if ok else resp_text[:60]}"
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        # Generic binary factory — "nhl" is stored as "hockey" in the DB
        generic_sports = ["tennis", "american_football", "hockey",
                          "cricket", "rugby", "baseball"]
        for sport in generic_sports:
            try:
                d = data[data["sport"] == sport].copy() if "sport" in data.columns else data
                completed = d[d["home_score"].notna()].shape[0]
                if completed < 200:
                    line = f"⏩ {sport.upper()}: skipped ({completed} rows)"
                    _log(line); logs.append(line); continue
                _log(f"🏋️  Training {sport} ({completed:,} rows)...")
                r = train_generic_sport_model(d, sport)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, resp_text = upload_model_to_render(r["path"], sport,
                                                        r["model_type"], r["accuracy"],
                                                        r["sample_size"])
                line = f"{'✅' if ok else '⚠️ '} {sport.upper()}: {r['accuracy']:.1%} | upload={'OK' if ok else resp_text[:60]}"
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        # Sync Render webhooks
        try:
            requests.post(f"{RENDER_URL}/api/admin/predict",
                          headers={"x-admin-key": ADMIN_KEY}, timeout=30)
            requests.post(f"{RENDER_URL}/api/admin/backfill-odds",
                          headers={"x-admin-key": ADMIN_KEY}, timeout=60)
            requests.post(f"{RENDER_URL}/api/admin/refresh-signals",
                          headers={"x-admin-key": ADMIN_KEY}, timeout=30)
            requests.post(f"{RENDER_URL}/api/admin/clear-train-flag",
                          headers={"x-admin-key": ADMIN_KEY}, timeout=10)
            line = "⚡ Predictions, odds backfill, and insider signals refreshed on Render."
            _log(line); logs.append(line)
        except Exception as e:
            line = f"⚠️  Remote webhook partial failure: {e}"
            _log(line); logs.append(line)

    finally:
        db.close()

    summary = "\n".join(logs)
    return summary, _get_log()

# ── Model status ──────────────────────────────────────────────────────────────
def check_model_status() -> tuple[str, str]:
    try:
        resp = requests.get(f"{RENDER_URL}/api/stats/backtest", timeout=20)
        if not resp.ok:
            return f"❌ {resp.status_code}", _get_log()
        models = resp.json().get("models", [])
        if not models:
            return "No models registered on Render yet.", _get_log()
        lines = [f"{'SPORT':<22} {'TYPE':<32} {'ROWS':>7} {'ACC':>7}  STATUS"]
        lines.append("-" * 74)
        for m in models:
            status = "🟢 ACTIVE" if m["active"] else "🔵"
            lines.append(
                f"{m['sport']:<22} {m['type']:<32} "
                f"{m['sample_size']:>7,} {m['accuracy']*100:>6.1f}%  {status}"
            )
        return "\n".join(lines), _get_log()
    except Exception as e:
        return f"❌ {e}", _get_log()

# ── Auto-polling ──────────────────────────────────────────────────────────────
def _poll_loop(interval: int = 30) -> None:
    _log(f"🔄 Auto-poll started (every {interval}s) — watching {RENDER_URL}")
    while not _poll_stop.is_set():
        try:
            resp = requests.get(
                f"{RENDER_URL}/api/admin/job-status",
                headers={"x-admin-key": ADMIN_KEY},
                timeout=15,
            )
            if resp.ok:
                s = resp.json()
                trigger = s.get("trigger_train", False)
                _log(
                    f"Poll: model={s.get('current_model_rows',0):,} rows | "
                    f"db={s.get('db_soccer_rows',0):,} rows | "
                    f"trigger={trigger} ({s.get('reason','none')})"
                )
                if trigger:
                    _log("🏋️  Trigger detected — running full training pipeline...")
                    run_training_pipeline()
                    _log("🏁 Auto-training complete.")
            else:
                _log(f"Poll: job-status returned {resp.status_code}")
        except Exception as e:
            _log(f"Poll error: {e}")
        _poll_stop.wait(interval)
    _log("⏹️ Auto-poll stopped.")

def toggle_auto_poll() -> tuple[str, str]:
    global _poll_thread, AUTO_POLL_ACTIVE
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    if _poll_thread and _poll_thread.is_alive():
        _poll_stop.set()
        AUTO_POLL_ACTIVE = False
        return "⏹️ Auto-poll stopping...", _get_log()
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(30,), daemon=True)
    _poll_thread.start()
    AUTO_POLL_ACTIVE = True
    return "✅ Auto-poll ENABLED (30s interval)", _get_log()

def get_log_refresh() -> str:
    return _get_log()

# ── Gradio UI ─────────────────────────────────────────────────────────────────
with gr.Blocks(title="REEDS AI Processing Core", theme=gr.themes.Soft()) as demo:

    gr.Markdown(
        """# 🟢 REEDS — LOYAL EDGE AI Engine
Background loop watches Render 24/7. Use the controls below for manual overrides.
> Set `DATABASE_URL`, `ADMIN_API_KEY`, `RENDER_URL` in **Space Settings → Repository secrets**."""
    )

    with gr.Row():
        btn_db      = gr.Button("🔍 Check DB Status",        variant="secondary")
        btn_ingest  = gr.Button("📥 Trigger Live Ingest",    variant="secondary")
        btn_train   = gr.Button("⚡ Train All Sports",        variant="primary")
        btn_status  = gr.Button("📈 Model Status on Render", variant="secondary")
        btn_toggle  = gr.Button("🔄 Toggle Auto-Poll",       variant="stop")
        btn_log     = gr.Button("🔃 Refresh Log",            variant="secondary")

    result_box = gr.Textbox(
        label="System Console Output", lines=14,
        interactive=False, placeholder="Awaiting system actions...",
    )
    log_box = gr.Textbox(
        label="Live log (last 200 lines)", lines=10,
        interactive=False,
    )

    btn_db.click(fn=check_database_status,  outputs=[result_box, log_box])
    btn_ingest.click(fn=trigger_remote_ingest, outputs=[result_box, log_box])
    btn_train.click(fn=run_training_pipeline,  outputs=[result_box, log_box])
    btn_status.click(fn=check_model_status,    outputs=[result_box, log_box])
    btn_toggle.click(fn=toggle_auto_poll,      outputs=[result_box, log_box])
    btn_log.click(fn=get_log_refresh,          outputs=[log_box])

    gr.Markdown(
        """---
**Workflow:** Check DB → (Ingest if empty) → Train All Sports → Toggle Auto-Poll  
Auto-poll retrains automatically when Render signals new data via `POST /api/admin/trigger-train-job`"""
    )

# ── Boot: auto-start poll if secrets are ready ────────────────────────────────
if DATABASE_URL:
    _log("Space booted with DATABASE_URL — starting auto-poll (30s)...")
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(30,), daemon=True)
    _poll_thread.start()
    AUTO_POLL_ACTIVE = True
else:
    _log("Space booted — set DATABASE_URL secret to enable auto-poll.")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
