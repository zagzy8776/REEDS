import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.services.predictions import generate_today_predictions


log = logging.getLogger(__name__)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def daily_job():
        db = SessionLocal()
        try:
            count = generate_today_predictions(db)
            log.info("Generated %s scheduled predictions", count)
        finally:
            db.close()

    scheduler.add_job(daily_job, "cron", hour=6, minute=0, id="daily_predictions", replace_existing=True)
    scheduler.start()
    return scheduler
