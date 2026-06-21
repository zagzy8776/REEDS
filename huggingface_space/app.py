"""
LOYAL EDGE — Hugging Face Space trainer
Always pulls latest code before importing, then trains all sports directly
against Neon. No dependency on Render for data or training.
"""
import os
import sys
import time
import subprocess
import threading
import requests
from pathlib import Path

import gradio as gr

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_KEY    = os.environ.get("ADMIN_API_KEY", "23235567Jjmt.")
RENDER_URL   = os.environ.get("RENDER_URL", "https://reeds-phj1.onrender.com").rstrip("/")

os.environ["DATABASE_URL"]      = DATABASE_URL
os.environ["MODEL_DIR"]         = "/tmp/models"
os.environ["MIN_TRAINING_ROWS"] = "200"
os.environ["APP_ENV"]           = "production"

os.makedirs("/tmp/models", exist_ok=True)

# ── Log buffer ────────────────────────────────────────────────────────────────
_log_lines: list[str] = []
_log_lock  = threading.Lock()

def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _log_lock:
        _log_lines.append(line)
        if len(_log_lines) > 800:
            _log_lines[:] = _log_lines[-500:]
    print(line, flush=True)

def _get_log() -> str:
    with _log_lock:
        return "\n".join(_log_lines[-300:])

# ── Repo bootstrap — always pull latest before importing ──────────────────────
REPO_DIR    = "/home/user/app/REEDS"
BACKEND_DIR = os.path.join(REPO_DIR, "backend")

def _ensure_repo() -> bool:
    """Clone or pull the repo, inject backend into sys.path. Returns True on success."""
    try:
        if not os.path.exists(REPO_DIR):
            _log("📥 Cloning zagzy8776/REEDS ...")
            result = subprocess.run(
                ["git", "clone", "https://github.com/zagzy8776/REEDS.git", REPO_DIR],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                _log(f"Clone error: {result.stderr[:200]}")
                return False
            _log("Clone complete.")
        else:
            _log("Pulling latest code...")
            subprocess.run(
                ["git", "-C", REPO_DIR, "fetch", "--all"],
                capture_output=True, timeout=30
            )
            subprocess.run(
                ["git", "-C", REPO_DIR, "reset", "--hard", "origin/main"],
                capture_output=True, timeout=30
            )
            _log("Pull complete.")

        if BACKEND_DIR not in sys.path:
            sys.path.insert(0, BACKEND_DIR)

        # Remove stale cached modules so re-imports pick up latest code
        stale = [k for k in sys.modules if k.startswith("app.")]
        for k in stale:
            del sys.modules[k]

        os.chdir(BACKEND_DIR)
        return True
    except Exception as e:
        _log(f"Repo setup error: {e}")
        return False

# Bootstrap on startup
_ensure_repo()

# ── DB helpers ────────────────────────────────────────────────────────────────
def _get_db_session():
    from app.db.session import SessionLocal, init_db
    init_db()
    return SessionLocal()

def _load_all_data(db):
    from app.services.predictions import dataframe_from_db
    data = dataframe_from_db(db, max_age_days=None)
    if "sport" in data.columns:
        data["sport"] = data["sport"].str.strip().str.lower()
    return data

# ── UI actions ────────────────────────────────────────────────────────────────

def action_check_db():
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    try:
        db = _get_db_session()
        data = _load_all_data(db)
        db.close()
        if data.empty:
            return "📁 Connected but empty — run 'Ingest Data' first.", _get_log()
        lines = [f"✅ Connected  |  Total rows: {len(data):,}\n"]
        for sport, grp in data.groupby("sport"):
            done = grp["home_score"].notna().sum()
            icon = "✅" if done >= 200 else "⚠️ "
            lines.append(f"  {icon} {sport:<22} {done:>6,} completed  ({len(grp):,} total)")
        return "\n".join(lines), _get_log()
    except Exception as e:
        return f"❌ DB error: {e}", _get_log()


def action_ingest_free(max_leagues: int):
    """Run ALL free scrapers directly against Neon from this Space."""
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()

    _ensure_repo()
    _log(f"Starting full free ingestion (max_leagues={int(max_leagues)})...")

    try:
        db = _get_db_session()
        from app.scraper.free_data import (
            ingest_football_data_co_uk,
            ingest_openfootball,
            ingest_tennis_atp,
            ingest_tennis_wta,
            ingest_tennis_data_co_uk,
            ingest_nba_github,
            ingest_nfl_spreadspoke,
            ingest_nhl_api,
            ingest_ipl_github,
            ingest_rugby_openfootball,
            ingest_mlb_retrosheet,
        )

        results = {}

        def _run(name, fn, *args):
            try:
                _log(f"  Ingesting {name}...")
                r = fn(db, *args)
                n = r.get("total", 0) if isinstance(r, dict) else r
                errs = len(r.get("errors", [])) if isinstance(r, dict) else 0
                results[name] = n
                _log(f"  {name}: {n:,} rows  ({errs} errors)")
            except Exception as e:
                results[name] = f"ERROR: {e}"
                _log(f"  {name}: ERROR — {e}")

        _run("soccer (football-data.co.uk)", ingest_football_data_co_uk,
             None, None, int(max_leagues))
        _run("soccer (openfootball)",        ingest_openfootball)
        _run("tennis ATP (JeffSackmann)",    ingest_tennis_atp)
        _run("tennis WTA (JeffSackmann)",    ingest_tennis_wta)
        _run("tennis (tennis-data.co.uk)",   ingest_tennis_data_co_uk)
        _run("basketball NBA (GitHub)",      ingest_nba_github)
        _run("american football NFL",        ingest_nfl_spreadspoke)
        _run("hockey NHL",                   ingest_nhl_api)
        _run("cricket IPL",                  ingest_ipl_github)
        _run("rugby",                        ingest_rugby_openfootball)
        _run("baseball MLB",                 ingest_mlb_retrosheet)

        db.close()

        # Summary
        lines = ["\n✅ Ingestion complete\n"]
        for name, val in results.items():
            lines.append(f"  {name}: {val}")
        summary = "\n".join(lines)
        _log(summary)
        return summary, _get_log()

    except Exception as e:
        _log(f"Ingest failed: {e}")
        return f"❌ Ingest failed: {e}", _get_log()


def action_train():
    """Pull latest code, ingest any new data, train all sports, upload to Render."""
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()

    _ensure_repo()

    _log("=" * 55)
    _log("⚡ LOYAL EDGE — Master Training Sequence")
    _log("=" * 55)

    db = _get_db_session()
    logs: list[str] = []

    try:
        from app.ml.train import (
            train_soccer_model,
            train_basketball_model,
            train_generic_sport_model,
        )
        from app.services.model_registry import register_model

        _log("Loading all training data (no date cap)...")
        data = _load_all_data(db)
        _log(f"Loaded {len(data):,} rows total")

        if data.empty:
            msg = "❌ Stopped: DB is empty — run Ingest Data first."
            _log(msg)
            return msg, _get_log()

        # Print sport breakdown
        if "sport" in data.columns:
            for sport, grp in data.groupby("sport"):
                done = grp["home_score"].notna().sum()
                _log(f"  {sport}: {done:,} completed rows")

        # ── Specialized trainers ───────────────────────────────────────────
        for sport, trainer in [("soccer", train_soccer_model),
                                ("basketball", train_basketball_model)]:
            try:
                d = data[data["sport"] == sport].copy()
                done = d["home_score"].notna().sum()
                if done < 200:
                    line = f"⏩ {sport.upper()}: skipped ({done:,} completed rows)"
                    _log(line); logs.append(line); continue
                _log(f"🏋️  Training {sport} ({done:,} rows)...")
                t0 = time.time()
                r = trainer(d)
                elapsed = int(time.time() - t0)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, resp_text = _upload(r["path"], sport, r["model_type"],
                                        r["accuracy"], r["sample_size"])
                line = (f"{'✅' if ok else '⚠️ '} {sport.upper()}: "
                        f"{r['accuracy']:.1%} acc  {r['sample_size']:,} rows  "
                        f"{elapsed}s  upload={'OK' if ok else resp_text[:80]}")
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        # ── Generic binary factory ─────────────────────────────────────────
        # "hockey" = NHL in the DB  |  never use "nhl" as the key
        for sport in ["tennis", "american_football", "hockey",
                      "cricket", "rugby", "baseball"]:
            try:
                d = data[data["sport"] == sport].copy()
                done = d["home_score"].notna().sum()
                if done < 200:
                    line = f"⏩ {sport.upper()}: skipped ({done:,} rows)"
                    _log(line); logs.append(line); continue
                _log(f"🏋️  Training {sport} ({done:,} rows)...")
                t0 = time.time()
                r = train_generic_sport_model(d, sport)
                elapsed = int(time.time() - t0)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, resp_text = _upload(r["path"], sport, r["model_type"],
                                        r["accuracy"], r["sample_size"])
                line = (f"{'✅' if ok else '⚠️ '} {sport.upper()}: "
                        f"{r['accuracy']:.1%} acc  {r['sample_size']:,} rows  "
                        f"{elapsed}s  upload={'OK' if ok else resp_text[:80]}")
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        # ── Render webhooks ────────────────────────────────────────────────
        for endpoint in ["/api/admin/predict",
                         "/api/admin/backfill-odds",
                         "/api/admin/refresh-signals",
                         "/api/admin/clear-train-flag"]:
            try:
                requests.post(f"{RENDER_URL}{endpoint}",
                              headers={"x-admin-key": ADMIN_KEY}, timeout=45)
            except Exception:
                pass
        line = "⚡ Render predictions + signals refreshed."
        _log(line); logs.append(line)

    finally:
        db.close()

    return "\n".join(logs), _get_log()


def action_model_status():
    try:
        resp = requests.get(f"{RENDER_URL}/api/stats/backtest", timeout=20)
        if not resp.ok:
            return f"❌ {resp.status_code}", _get_log()
        models = resp.json().get("models", [])
        if not models:
            return "No models on Render yet.", _get_log()
        lines = [f"{'SPORT':<22} {'TYPE':<30} {'ROWS':>7}  {'ACC':>6}  STATUS"]
        lines.append("-" * 72)
        for m in models:
            status = "🟢 ACTIVE" if m["active"] else "🔵"
            lines.append(f"{m['sport']:<22} {m['type']:<30} "
                         f"{m['sample_size']:>7,}  {m['accuracy']*100:>5.1f}%  {status}")
        return "\n".join(lines), _get_log()
    except Exception as e:
        return f"❌ {e}", _get_log()


def action_wake_render():
    """Ping Render's /api/wake to keep it alive and sync live scores."""
    try:
        resp = requests.get(f"{RENDER_URL}/api/wake", timeout=30)
        if resp.ok:
            d = resp.json()
            msg = (f"✅ Render is awake\n"
                   f"  scores synced: {d.get('scores_synced', {})}\n"
                   f"  predictions generated: {d.get('generated', 0)}")
        else:
            msg = f"⚠️  Render returned {resp.status_code}"
    except Exception as e:
        msg = f"❌ Could not reach Render: {e}"
    _log(msg)
    return msg, _get_log()


# ── Upload with retry ─────────────────────────────────────────────────────────
def _upload(path: str, sport: str, model_type: str,
            accuracy: float, sample_size: int) -> tuple[bool, str]:
    for attempt in range(3):
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{RENDER_URL}/api/admin/upload-model",
                    headers={"x-admin-key": ADMIN_KEY},
                    files={"model": (Path(path).name, f, "application/octet-stream")},
                    data={"sport": sport, "model_type": model_type,
                          "accuracy": str(accuracy), "sample_size": str(sample_size)},
                    timeout=300,
                )
            if r.ok:
                return True, r.text
            if r.status_code == 413:
                return False, "413 Too Large"
            if r.status_code in (401, 403):
                return False, f"{r.status_code} Auth error"
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            return False, str(e)
        time.sleep(2 ** attempt)
    return False, f"failed after 3 attempts"


# ── Auto-poll ─────────────────────────────────────────────────────────────────
_poll_stop   = threading.Event()
_poll_thread: threading.Thread | None = None

def _poll_loop(interval: int) -> None:
    _log(f"🔄 Auto-poll started (every {interval}s)")
    while not _poll_stop.is_set():
        try:
            resp = requests.get(f"{RENDER_URL}/api/admin/job-status",
                                headers={"x-admin-key": ADMIN_KEY}, timeout=15)
            if resp.ok:
                s = resp.json()
                trigger = s.get("trigger_train", False)
                _log(f"Poll: model={s.get('current_model_rows',0):,} rows | "
                     f"db={s.get('db_soccer_rows',0):,} rows | "
                     f"trigger={trigger} ({s.get('reason','none')})")
                if trigger:
                    _log("🏋️  Trigger — running training pipeline...")
                    action_train()
                    _log("🏁 Auto-training complete.")
        except Exception as e:
            _log(f"Poll error: {e}")
        _poll_stop.wait(interval)
    _log("⏹️ Poll stopped.")

def action_toggle_poll():
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        _poll_stop.set()
        return "⏹️ Auto-poll stopping...", _get_log()
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(30,), daemon=True)
    _poll_thread.start()
    return "✅ Auto-poll ENABLED (30s)", _get_log()

def action_refresh_log():
    return _get_log()


# ── Gradio UI ─────────────────────────────────────────────────────────────────
with gr.Blocks(title="LOYAL EDGE Trainer", theme=gr.themes.Soft()) as demo:

    gr.Markdown("""# 🏆 LOYAL EDGE — AI Engine
Trains all sport models directly against Neon and pushes them live to Render.
> Add `DATABASE_URL`, `ADMIN_API_KEY`, `RENDER_URL` in **Space Settings → Repository secrets**""")

    # Row 1 — status
    with gr.Row():
        btn_db     = gr.Button("🔍 Check DB",          variant="secondary")
        btn_status = gr.Button("📈 Model Status",      variant="secondary")
        btn_log    = gr.Button("🔃 Refresh Log",       variant="secondary")
        btn_wake   = gr.Button("🌐 Wake Render",       variant="secondary")

    # Row 2 — data ingestion
    gr.Markdown("### 📥 Ingest Historical Data (runs directly against Neon — no Render needed)")
    with gr.Row():
        ingest_slider = gr.Slider(1, 21, value=21, step=1,
                                  label="Max soccer leagues (each ~60s)", scale=3)
        btn_ingest = gr.Button("⬇️ Ingest ALL Sports Data", variant="primary", scale=1)

    # Row 3 — training
    gr.Markdown("### 🏋️ Train All Sports")
    gr.Markdown("_Soccer · Basketball · Tennis · NFL · NHL · Cricket · Rugby · Baseball — skips < 200 rows_")
    with gr.Row():
        btn_train  = gr.Button("🚀 Train All + Upload to Render", variant="primary", scale=2)
        btn_poll   = gr.Button("🔄 Toggle Auto-Poll (30s)",       variant="stop",    scale=1)

    result_box = gr.Textbox(label="Output", lines=18, interactive=False)
    log_box    = gr.Textbox(label="Live log", lines=10, interactive=False)

    btn_db.click(action_check_db,           outputs=[result_box, log_box])
    btn_status.click(action_model_status,   outputs=[result_box, log_box])
    btn_log.click(action_refresh_log,       outputs=[log_box])
    btn_wake.click(action_wake_render,      outputs=[result_box, log_box])
    btn_ingest.click(action_ingest_free,    inputs=[ingest_slider], outputs=[result_box, log_box])
    btn_train.click(action_train,           outputs=[result_box, log_box])
    btn_poll.click(action_toggle_poll,      outputs=[result_box, log_box])

    gr.Markdown("""---
**Workflow:**
1. Click **Check DB** — see current row counts
2. Click **⬇️ Ingest ALL Sports Data** — pulls free data for every sport directly into Neon
3. Click **🚀 Train All + Upload** — trains models with full history, uploads to Render
4. Click **🔄 Toggle Auto-Poll** — keeps Space running, auto-retrains on demand""")


# ── Auto-start poll on boot ───────────────────────────────────────────────────
if DATABASE_URL:
    _log("Space ready — auto-poll starting (30s interval)...")
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(30,), daemon=True)
    _poll_thread.start()
else:
    _log("Space ready — set DATABASE_URL secret then use the buttons.")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
