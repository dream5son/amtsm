import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.engine.backtest_tasks import backtest_worker_task
from app.engine.tasks import (
    baseline_precompute_task,
    bootstrap_watchlist_snapshot,
    daily_snapshot_task,
    intraday_snapshot_task,
    market_polling_task,
)

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def create_scheduler() -> BackgroundScheduler:
    global _scheduler
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
    scheduler.add_job(
        intraday_snapshot_task,
        trigger="interval",
        seconds=settings.intraday_snapshot_interval_seconds,
        id="intraday_snapshot",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        backtest_worker_task,
        trigger="interval",
        seconds=settings.backtest_worker_interval_seconds,
        id="backtest_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler = scheduler
    return scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def enqueue_snapshot_bootstrap(stock_code: str) -> None:
    """Schedule a one-shot snapshot bootstrap for a newly added watchlist stock.

    When the scheduler is not running (e.g. unit tests), this is a no-op so that
    add_watchlist stays free of network side effects. Call
    ``bootstrap_watchlist_snapshot`` directly in tests that need coverage.
    """
    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        logger.debug(
            "enqueue_snapshot_bootstrap skipped (scheduler not running) code=%s",
            stock_code,
        )
        return

    scheduler.add_job(
        bootstrap_watchlist_snapshot,
        args=[stock_code],
        id=f"snapshot_bootstrap_{stock_code}",
        replace_existing=True,
    )
