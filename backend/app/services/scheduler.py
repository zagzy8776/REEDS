import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.models import BacktestRun
from app.db.session import SessionLocal
from app.ml.backtest import walk_forward_backtest
from app.ml.calibration import fit_soccer_platt_calibrator
from app.ml.train import train_basketball_model, train_soccer_model
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions


log = logging.getLogger(__name__)


def run_daily_learning_pipeline() -> dict:
    """Run the full unattended daily model refresh pipeline.

    Order matters:
    1. Train/register fresh model candidates.
    2. Refresh calibration from the same current database boundary.
    3. Persist walk-forward audit rows where possible.
    4. Generate customer-facing predictions from active models.

    Each stage records failures without hiding earlier successful stages. This keeps
    the scheduler resilient while making deployment logs useful for operations.
    """

    db = SessionLocal()
    report: dict = {"trained": [], "calibrated": None, "backtests": [], "skipped": [], "generated_predictions": 0}
    try:
        data = dataframe_from_db(db)

        for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
            try:
                result = trainer(data)
                mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
                report["trained"].append({"sport": sport, **result, "active": mv.is_active})
            except Exception as exc:  # noqa: BLE001 - scheduler must log and continue safely
                log.exception("Daily %s training failed", sport)
                report["skipped"].append({"stage": "train", "sport": sport, "reason": str(exc)})

        try:
            report["calibrated"] = fit_soccer_platt_calibrator(data[data.get("sport", "soccer") == "soccer"].copy())
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily soccer calibration failed")
            report["skipped"].append({"stage": "calibrate", "sport": "soccer", "reason": str(exc)})

        for sport in ("soccer", "basketball"):
            try:
                result = walk_forward_backtest(data[data.get("sport", sport) == sport].copy(), sport)
                run = BacktestRun(
                    sport=sport,
                    model_type=result["model_type"],
                    split_strategy=result["split_strategy"],
                    sample_size=result["sample_size"],
                    accuracy=result["accuracy"],
                    brier_score=result["brier_score"],
                    log_loss=result["log_loss"],
                    metrics=result["metrics"],
                )
                db.add(run)
                db.commit()
                db.refresh(run)
                report["backtests"].append({"id": run.id, **result})
            except Exception as exc:  # noqa: BLE001
                log.exception("Daily %s backtest failed", sport)
                report["skipped"].append({"stage": "backtest", "sport": sport, "reason": str(exc)})

        try:
            report["generated_predictions"] = generate_today_predictions(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily prediction generation failed")
            report["skipped"].append({"stage": "predict", "reason": str(exc)})

        return report
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def daily_job():
        report = run_daily_learning_pipeline()
        log.info("Daily learning pipeline completed: %s", report)

    scheduler.add_job(
        daily_job,
        "cron",
        hour=6,
        minute=0,
        id="daily_learning_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
