"""LOYAL EDGE — Hugging Face training worker.

The Space is the ML worker. Render serves the public API; Neon is the source of
truth. This worker pulls the latest REEDS code, trains the available sport
models from completed historical data, publishes model artifacts to Render, and
polls Render for retraining signals.
"""

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
RENDER_URL = os.environ.get("RENDER_URL", "https://reeds-phj1.onrender.com").rstrip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "zagzy8776/REEDS")

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["MODEL_DIR"] = "/tmp/models"
os.environ["MIN_TRAINING_ROWS"] = "200"
os.environ["APP_ENV"] = "production"
os.makedirs("/tmp/models", exist_ok=True)

# These are the sports for which the current REEDS training code has either a
# dedicated trainer or a generic production trainer and matching historical data.
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
    if not ADMIN_KEY:
        return {}
    return {"x-admin-key": ADMIN_KEY}


def _upload_to_render(path: str, sport: str, model_type: str, accuracy: float, sample_size: int) -> tuple[bool, str]:
    """Upload a small model directly; use a GitHub Release as a durable fallback."""
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
            f"{RENDER_URL}/api/admin/download-models",
            headers=_render_headers(),
            json={},
            timeout=60,
        )
        if pull.ok:
            return True, f"GitHub Release fallback OK ({tag})"
        return False, f"release uploaded but Render pull returned HTTP {pull.status_code}"
    except Exception as exc:
        return False, f"release fallback error: {exc}"


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
            completed = int(group["home_score"].notna().sum())
            lines.append(f"  {sport:<20} {completed:>7,} completed / {len(group):>7,} total")
        return "\n".join(lines), _get_log()
    except Exception as exc:
        return f"❌ DB error: {exc}", _get_log()
    finally:
        if db is not None:
            db.close()


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
                results.append(f"{name}: ERROR {exc}")
                _log(f"  ❌ {results[-1]}")
        return "\n".join(["✅ Ingestion finished", *results]), _get_log()
    except Exception as exc:
        return f"❌ Ingestion failed: {exc}", _get_log()
    finally:
        if db is not None:
            db.close()


def action_train():
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret is missing", _get_log()
    if not ADMIN_KEY:
        return "❌ ADMIN_API_KEY secret is missing", _get_log()
    if not _train_lock.acquire(blocking=False):
        return "⏳ Training is already running", _get_log()

    db = None
    logs: list[str] = []
    try:
        if not _ensure_repo():
            return "❌ Could not load current REEDS code", _get_log()
        from app.ml.train import train_soccer_model, train_basketball_model, train_generic_sport_model
        from app.services.model_registry import register_model

        db = _get_db()
        data = _load_data(db)
        if data.empty:
            return "❌ Neon has no training data. Run ingestion first.", _get_log()

        _log(f"⚡ LOYAL EDGE master training | {len(data):,} rows")
        completed_by_sport = {
            sport: int(group["home_score"].notna().sum())
            for sport, group in data.groupby("sport")
        }

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
            trainer = trainers[sport]
            completed = completed_by_sport.get(sport, 0)
            if completed < 200:
                line = f"⏩ {sport.upper()}: skipped — only {completed:,} completed rows (need 200)"
                _log(line); logs.append(line); continue
            try:
                frame = data[data["sport"] == sport].copy()
                frame = frame[frame["home_score"].notna() & frame["away_score"].notna()].copy()
                _log(f"🏋️ Training {sport} on {len(frame):,} completed rows...")
                started = time.time()
                result = trainer(frame)
                elapsed = int(time.time() - started)
                register_model(db, sport, str(result["model_type"])[:50], result["path"], result["accuracy"], result["sample_size"])
                ok, detail = _upload_to_render(
                    result["path"], sport, result["model_type"], result["accuracy"], result["sample_size"]
                )
                line = (
                    f"{'✅' if ok else '⚠️'} {sport.upper()}: "
                    f"accuracy={result['accuracy']:.1%} rows={result['sample_size']:,} "
                    f"time={elapsed}s upload={'OK' if ok else detail[:100]}"
                )
                _log(line); logs.append(line)
            except Exception as exc:
                line = f"❌ {sport.upper()}: {exc}"
                _log(line); logs.append(line)

        # Refresh the public board after all models have landed. The Render-side
        # prediction guard makes this safe even if another trigger fires.
        for endpoint in ("/api/admin/predict", "/api/admin/backfill-odds", "/api/admin/clear-train-flag"):
            try:
                response = requests.post(f"{RENDER_URL}{endpoint}", headers=_render_headers(), timeout=90)
                _log(f"Render {endpoint}: HTTP {response.status_code}")
            except Exception as exc:
                _log(f"Render {endpoint}: {exc}")
        logs.append("⚡ Render synchronization requested")
        return "\n".join(logs), _get_log()
    finally:
        if db is not None:
            db.close()
        _train_lock.release()


def action_model_status():
    try:
        response = requests.get(f"{RENDER_URL}/api/stats/backtest", timeout=20)
        if not response.ok:
            return f"❌ Render HTTP {response.status_code}", _get_log()
        models = response.json().get("models", [])
        if not models:
            return "No active models on Render yet.", _get_log()
        lines = ["SPORT                  TYPE                           ROWS     ACC  STATUS"]
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
    try:
        response = requests.get(f"{RENDER_URL}/api/wake", timeout=60)
        if response.ok:
            data = response.json()
            message = (
                "✅ Render awake\n"
                f"scores synced: {data.get('scores_synced', {})}\n"
                f"predictions generated: {data.get('generated', 0)}"
            )
        else:
            message = f"⚠️ Render returned HTTP {response.status_code}"
    except Exception as exc:
        message = f"❌ {exc}"
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
                    if status.get("trigger_train"):
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
Trains soccer, basketball, tennis, NFL, NHL, cricket, rugby and baseball models against Neon and publishes them to Render.

**Required Space secrets:** `DATABASE_URL` · `ADMIN_API_KEY` · `RENDER_URL` · optional `GITHUB_TOKEN` for durable model fallback.
"""
    )

    with gr.Row():
        btn_pull = gr.Button("🔄 Force Pull Latest Code", variant="stop", scale=2)
        btn_db = gr.Button("🔍 Check DB", variant="secondary")
        btn_status = gr.Button("📈 Model Status", variant="secondary")
        btn_wake = gr.Button("🌐 Wake Render", variant="secondary")
        btn_log = gr.Button("🔃 Refresh Log", variant="secondary")

    gr.Markdown("### 📥 Step 1 — Ingest historical data")
    with gr.Row():
        ingest_slider = gr.Slider(1, 21, value=21, step=1, label="Max soccer leagues", scale=3)
        btn_ingest = gr.Button("⬇️ Ingest ALL Sports", variant="primary")

    gr.Markdown("### 🏋️ Step 2 — Train and publish models")
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
    btn_ingest.click(action_ingest, inputs=[ingest_slider], outputs=[result_box, log_box])
    btn_train.click(action_train, outputs=[result_box, log_box])
    btn_poll.click(action_toggle_poll, outputs=[result_box, log_box])

    gr.Markdown("---\n**Workflow:** Force Pull → Check DB → Ingest → Train All → Model Status → Auto-Poll")


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
