from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.engine.tasks import baseline_precompute_task, daily_snapshot_task, market_polling_task


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        baseline_precompute_task,
        trigger="cron",
        hour=settings.baseline_hour,
        minute=settings.baseline_minute,
        id="baseline_precompute",
        replace_existing=True,
    )
    scheduler.add_job(
        daily_snapshot_task,
        trigger="cron",
        hour=settings.snapshot_hour,
        minute=settings.snapshot_minute,
        id="daily_snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        market_polling_task,
        trigger="interval",
        seconds=settings.polling_interval_seconds,
        id="market_polling",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
