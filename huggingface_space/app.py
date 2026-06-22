"""
LOYAL EDGE — Hugging Face Space trainer  v4
Trains all sport models directly against Neon and pushes them live to Render.

CRITICAL: This file is the Space's app.py. When you update the REEDS GitHub
repo, you must ALSO push this file to the HF Space repo to trigger a restart.
The Space does NOT auto-restart when zagzy8776/REEDS is updated.

Push to HF Space:
    cd huggingface_space
    git remote add space https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE
    git push space main
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

# ── Repo bootstrap ────────────────────────────────────────────────────────────
REPO_DIR    = "/home/user/app/REEDS"
BACKEND_DIR = os.path.join(REPO_DIR, "backend")

def _ensure_repo(force: bool = False) -> bool:
    """Clone or hard-reset the repo, flush stale module cache."""
    try:
        if not os.path.exists(REPO_DIR) or force:
            if os.path.exists(REPO_DIR):
                subprocess.run(["rm", "-rf", REPO_DIR], timeout=30)
            _log("📥 Cloning zagzy8776/REEDS (latest)...")
            r = subprocess.run(
                ["git", "clone", "--depth=1",
                 "https://github.com/zagzy8776/REEDS.git", REPO_DIR],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode != 0:
                _log(f"Clone error: {r.stderr[:300]}")
                return False
            _log("Clone complete.")
        else:
            _log("Pulling latest from zagzy8776/REEDS...")
            subprocess.run(["git", "-C", REPO_DIR, "fetch", "--all"],
                           capture_output=True, timeout=30)
            r = subprocess.run(
                ["git", "-C", REPO_DIR, "reset", "--hard", "origin/main"],
                capture_output=True, text=True, timeout=30,
            )
            _log(f"Pull: {r.stdout.strip() or 'done'}")

        # Flush ALL cached app.* modules so re-imports get the fresh code
        stale = [k for k in list(sys.modules) if k.startswith("app.") or k == "app"]
        for k in stale:
            del sys.modules[k]

        if BACKEND_DIR not in sys.path:
            sys.path.insert(0, BACKEND_DIR)
        # Note: do NOT os.chdir here — it breaks subsequent git operations
        # that rely on the original working directory

        # Verify the multi_class fix is present
        cal_path = os.path.join(BACKEND_DIR, "app/ml/calibration.py")
        if os.path.exists(cal_path):
            cal_src = open(cal_path).read()
            if "multi_class" in cal_src:
                _log("⚠️  calibration.py still has multi_class — patching now...")
                patched = cal_src.replace(
                    'LogisticRegression(max_iter=1000, multi_class="ovr")',
                    'LogisticRegression(max_iter=1000, solver="lbfgs")'
                )
                open(cal_path, "w").write(patched)
                _log("Patched calibration.py")
            else:
                _log("✅ calibration.py clean (no multi_class)")

        return True
    except Exception as e:
        _log(f"Repo setup error: {e}")
        return False

# Run on startup
_ensure_repo()

# ── DB helpers ────────────────────────────────────────────────────────────────
def _get_db():
    from app.db.session import SessionLocal, init_db
    init_db()
    return SessionLocal()

def _load_data(db):
    from app.services.predictions import dataframe_from_db
    data = dataframe_from_db(db, max_age_days=None)
    if "sport" in data.columns:
        data["sport"] = data["sport"].str.strip().str.lower()
    return data

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
                return False, f"413 Too Large — model file too big for Render free tier"
            if r.status_code in (401, 403):
                return False, f"{r.status_code} Auth error — check ADMIN_API_KEY"
            # Log the actual error response for debugging
            _log(f"Upload {sport} attempt {attempt+1}: HTTP {r.status_code} — {r.text[:200]}")
        except requests.exceptions.Timeout:
            _log(f"Upload {sport} timeout (attempt {attempt+1}/3)")
        except Exception as e:
            _log(f"Upload {sport} exception (attempt {attempt+1}): {e}")
        time.sleep(2 ** attempt)
    return False, "failed after 3 attempts"

# ── Actions ───────────────────────────────────────────────────────────────────

def action_force_pull():
    """Delete and re-clone the REEDS repo — guarantees latest code."""
    _log("🔄 Force-pulling latest REEDS code...")
    ok = _ensure_repo(force=True)
    if ok:
        msg = "✅ Force-pull complete — all fixes are now active."
    else:
        msg = "❌ Force-pull failed — check log below."
    return msg, _get_log()


def action_check_db():
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    _ensure_repo()
    try:
        db = _get_db()
        data = _load_data(db)
        db.close()
        if data.empty:
            return "📁 Connected but empty — click Ingest Data first.", _get_log()
        lines = [f"✅ Connected  |  Total rows: {len(data):,}\n"]
        for sport, grp in data.groupby("sport"):
            done = grp["home_score"].notna().sum()
            icon = "✅" if done >= 200 else "⚠️ "
            lines.append(f"  {icon} {sport:<22} {done:>6,} completed  ({len(grp):,} total)")
        return "\n".join(lines), _get_log()
    except Exception as e:
        return f"❌ DB error: {e}", _get_log()


def action_ingest(max_leagues: int):
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    _ensure_repo()
    _log(f"Starting full free ingestion (max_leagues={int(max_leagues)})...")
    try:
        db = _get_db()
        from app.scraper.free_data import (
            ingest_football_data_co_uk, ingest_openfootball,
            ingest_tennis_atp, ingest_tennis_wta, ingest_tennis_data_co_uk,
            ingest_nba_github, ingest_nfl_spreadspoke, ingest_nhl_api,
            ingest_ipl_github, ingest_rugby_openfootball, ingest_mlb_retrosheet,
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

        _run("soccer (football-data.co.uk)", ingest_football_data_co_uk, None, None, int(max_leagues))
        _run("soccer (openfootball)",        ingest_openfootball)
        _run("tennis ATP",                   ingest_tennis_atp)
        _run("tennis WTA",                   ingest_tennis_wta)
        _run("tennis data.co.uk",            ingest_tennis_data_co_uk)
        _run("basketball NBA",               ingest_nba_github)
        _run("american football NFL",        ingest_nfl_spreadspoke)
        _run("hockey NHL",                   ingest_nhl_api)
        _run("cricket IPL",                  ingest_ipl_github)
        _run("rugby",                        ingest_rugby_openfootball)
        _run("baseball MLB",                 ingest_mlb_retrosheet)
        db.close()

        lines = ["\n✅ Ingestion complete\n"]
        for k, v in results.items():
            lines.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
        summary = "\n".join(lines)
        _log(summary)
        return summary, _get_log()
    except Exception as e:
        _log(f"Ingest failed: {e}")
        return f"❌ {e}", _get_log()


def action_train():
    if not DATABASE_URL:
        return "❌ DATABASE_URL secret not set.", _get_log()
    _ensure_repo()
    _log("=" * 55)
    _log("⚡ LOYAL EDGE — Master Training Sequence")
    _log("=" * 55)

    db = _get_db()
    logs: list[str] = []
    try:
        from app.ml.train import (
            train_soccer_model, train_basketball_model, train_generic_sport_model,
        )
        from app.services.model_registry import register_model

        _log("Loading all training data (no date cap)...")
        data = _load_data(db)
        _log(f"Loaded {len(data):,} rows")
        if data.empty:
            return "❌ DB empty — run Ingest first.", _get_log()

        for sport, grp in data.groupby("sport"):
            done = grp["home_score"].notna().sum()
            _log(f"  {sport}: {done:,} completed rows")

        for sport, trainer in [("soccer", train_soccer_model),
                                ("basketball", train_basketball_model)]:
            try:
                d = data[data["sport"] == sport].copy()
                done = d["home_score"].notna().sum()
                if done < 200:
                    line = f"⏩ {sport.upper()}: skipped ({done:,} rows)"
                    _log(line); logs.append(line); continue
                # Pass only completed fixtures — trainers skip null-score rows
                # internally anyway, but this surfaces the real count early.
                d_completed = d[d["home_score"].notna() & d["away_score"].notna()].copy()
                _log(f"🏋️  Training {sport} ({done:,} completed rows)...")
                t0 = time.time()
                r = trainer(d_completed)
                elapsed = int(time.time() - t0)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, txt = _upload(r["path"], sport, r["model_type"],
                                  r["accuracy"], r["sample_size"])
                line = (f"{'✅' if ok else '⚠️ '} {sport.upper()}: "
                        f"{r['accuracy']:.1%}  {r['sample_size']:,} rows  "
                        f"{elapsed}s  upload={'OK' if ok else txt[:80]}")
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        # hockey = NHL in the DB  |  never use "nhl"
        for sport in ["tennis", "american_football", "hockey",
                      "cricket", "rugby", "baseball"]:
            try:
                d = data[data["sport"] == sport].copy()
                done = d["home_score"].notna().sum()
                if done < 200:
                    line = f"⏩ {sport.upper()}: skipped ({done:,} rows)"
                    _log(line); logs.append(line); continue
                # Pass only completed fixtures to avoid spurious low row count
                d_completed = d[d["home_score"].notna() & d["away_score"].notna()].copy()
                _log(f"🏋️  Training {sport} ({done:,} completed rows)...")
                t0 = time.time()
                r = train_generic_sport_model(d_completed, sport)
                elapsed = int(time.time() - t0)
                register_model(db, sport, r["model_type"], r["path"],
                               r["accuracy"], r["sample_size"])
                ok, txt = _upload(r["path"], sport, r["model_type"],
                                  r["accuracy"], r["sample_size"])
                line = (f"{'✅' if ok else '⚠️ '} {sport.upper()}: "
                        f"{r['accuracy']:.1%}  {r['sample_size']:,} rows  "
                        f"{elapsed}s  upload={'OK' if ok else txt[:80]}")
                _log(line); logs.append(line)
            except Exception as e:
                line = f"❌ {sport.upper()}: {e}"
                _log(line); logs.append(line)

        for ep in ["/api/admin/predict", "/api/admin/backfill-odds",
                   "/api/admin/refresh-signals", "/api/admin/clear-train-flag"]:
            try:
                requests.post(f"{RENDER_URL}{ep}",
                              headers={"x-admin-key": ADMIN_KEY}, timeout=45)
            except Exception:
                pass
        line = "⚡ Render synced (predictions + signals + clear flag)"
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
    try:
        resp = requests.get(f"{RENDER_URL}/api/wake", timeout=30)
        if resp.ok:
            d = resp.json()
            msg = (f"✅ Render awake\n"
                   f"  scores synced: {d.get('scores_synced', {})}\n"
                   f"  predictions: {d.get('generated', 0)}")
        else:
            msg = f"⚠️  Render returned {resp.status_code}"
    except Exception as e:
        msg = f"❌ {e}"
    _log(msg)
    return msg, _get_log()


def action_refresh_log():
    return _get_log()

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
                _log(f"Poll: model={s.get('current_model_rows',0):,} | "
                     f"db={s.get('db_soccer_rows',0):,} | "
                     f"trigger={trigger} ({s.get('reason','none')})")
                if trigger:
                    _log("🏋️  Trigger — training...")
                    action_train()
                    _log("🏁 Auto-training done.")
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

# ── Gradio UI ─────────────────────────────────────────────────────────────────
with gr.Blocks(title="LOYAL EDGE Trainer", theme=gr.themes.Soft()) as demo:

    gr.Markdown("""# 🏆 LOYAL EDGE — AI Engine
Trains all sport models directly against Neon and pushes them live to Render.
> Secrets needed in Space Settings: `DATABASE_URL` · `ADMIN_API_KEY` · `RENDER_URL`""")

    # Row 1 — maintenance
    with gr.Row():
        btn_pull   = gr.Button("🔄 Force Pull Latest Code", variant="stop",      scale=2)
        btn_db     = gr.Button("🔍 Check DB",               variant="secondary",  scale=1)
        btn_status = gr.Button("📈 Model Status",           variant="secondary",  scale=1)
        btn_wake   = gr.Button("🌐 Wake Render",            variant="secondary",  scale=1)
        btn_log    = gr.Button("🔃 Refresh Log",            variant="secondary",  scale=1)

    # Row 2 — ingest
    gr.Markdown("### 📥 Step 1 — Ingest Historical Data (runs inside Space, writes directly to Neon)")
    with gr.Row():
        ingest_slider = gr.Slider(1, 21, value=21, step=1,
                                  label="Max soccer leagues (21 = all)", scale=3)
        btn_ingest = gr.Button("⬇️ Ingest ALL Sports", variant="primary", scale=1)

    # Row 3 — train
    gr.Markdown("### 🏋️ Step 2 — Train & Upload (soccer · basketball · tennis · NFL · NHL · cricket · rugby · baseball)")
    with gr.Row():
        btn_train = gr.Button("🚀 Train All + Upload to Render", variant="primary", scale=2)
        btn_poll  = gr.Button("🔄 Toggle Auto-Poll (30s)",       variant="stop",    scale=1)

    result_box = gr.Textbox(label="Output",   lines=18, interactive=False)
    log_box    = gr.Textbox(label="Live log", lines=10, interactive=False)

    btn_pull.click(action_force_pull,    outputs=[result_box, log_box])
    btn_db.click(action_check_db,        outputs=[result_box, log_box])
    btn_status.click(action_model_status,outputs=[result_box, log_box])
    btn_wake.click(action_wake_render,   outputs=[result_box, log_box])
    btn_log.click(action_refresh_log,    outputs=[log_box])
    btn_ingest.click(action_ingest,      inputs=[ingest_slider], outputs=[result_box, log_box])
    btn_train.click(action_train,        outputs=[result_box, log_box])
    btn_poll.click(action_toggle_poll,   outputs=[result_box, log_box])

    gr.Markdown("""---
**Workflow:** 🔄 Force Pull → 🔍 Check DB → ⬇️ Ingest ALL Sports → 🚀 Train All + Upload → 🔄 Toggle Auto-Poll""")

# ── Boot ──────────────────────────────────────────────────────────────────────
if DATABASE_URL:
    _log("Space booted — starting auto-poll (30s)...")
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(30,), daemon=True)
    _poll_thread.start()
else:
    _log("Space booted — add DATABASE_URL secret to enable DB operations.")

if __name__ == "__main__":
    demo.launch(share=False)
