import logging
from datetime import datetime
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.engine.activity_log import (
    emit,
    install_activity_logging,
    map_scheduler_job_id,
    track_job,
)
from app.engine.backtest_tasks import backtest_worker_task
from app.engine.market_hours import SH_TZ
from app.engine.tasks import (
    baseline_precompute_task,
    bootstrap_watchlist_snapshot,
    daily_snapshot_task,
    market_polling_task,
    startup_market_data_catchup,
)

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _on_job_error(event: JobExecutionEvent) -> None:
    job_id = event.job_id or ""
    job = map_scheduler_job_id(job_id)
    if job is None:
        return
    exception = event.exception
    emit("error", f"{job_id} 执行失败: {exception}", job=job)


def create_scheduler() -> BackgroundScheduler:
    global _scheduler
    install_activity_logging()
    now = datetime.now(SH_TZ)
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        track_job("baseline_precompute", baseline_precompute_task),
        trigger="cron",
        hour=settings.baseline_hour,
        minute=settings.baseline_minute,
        id="baseline_precompute",
        replace_existing=True,
    )
    scheduler.add_job(
        track_job("daily_snapshot", daily_snapshot_task),
        trigger="cron",
        hour=settings.snapshot_hour,
        minute=settings.snapshot_minute,
        id="daily_snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        track_job("market_polling", market_polling_task),
        trigger="interval",
        seconds=settings.polling_interval_seconds,
        id="market_polling",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
        misfire_grace_time=None,
    )
    scheduler.add_job(
        track_job("backtest_worker", backtest_worker_task),
        trigger="interval",
        seconds=settings.backtest_worker_interval_seconds,
        id="backtest_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        track_job("startup_market_data_catchup", startup_market_data_catchup),
        trigger="date",
        run_date=now,
        id="startup_market_data_catchup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    _scheduler = scheduler
    return scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def _iso_sh(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SH_TZ)
    return dt.astimezone(SH_TZ).isoformat(timespec="seconds")


def list_activity_jobs() -> list[dict[str, Any]]:
    """Describe APScheduler jobs for the homepage activity panel.

    Persistent jobs come from ``create_scheduler``; ``snapshot_bootstrap`` is
    on-demand (``enqueue_snapshot_bootstrap``). Intraday snapshot is no longer
    a separate job — it is throttled inside market polling.
    """
    scheduler = get_scheduler()
    live = list(scheduler.get_jobs()) if scheduler is not None else []
    by_id = {job.id: job for job in live}

    def next_run(job_id: str) -> str | None:
        job = by_id.get(job_id)
        if job is None:
            return None
        return _iso_sh(getattr(job, "next_run_time", None))

    bootstrap_jobs = [
        job for job in live if map_scheduler_job_id(job.id) == "snapshot_bootstrap"
    ]
    pending: list[str] = []
    for job in bootstrap_jobs:
        code = job.id.removeprefix("snapshot_bootstrap_")
        if code and code != job.id:
            pending.append(code)
        elif job.args:
            pending.append(str(job.args[0]))
    bootstrap_times = [
        run_at
        for job in bootstrap_jobs
        if (run_at := getattr(job, "next_run_time", None)) is not None
    ]

    return [
        {
            "id": "market_polling",
            "kind": "interval",
            "interval_seconds": settings.polling_interval_seconds,
            "note": "含盘中快照",
        },
        {
            "id": "baseline_precompute",
            "kind": "cron",
            "hour": settings.baseline_hour,
            "minute": settings.baseline_minute,
            "next_run_time": next_run("baseline_precompute"),
        },
        {
            "id": "daily_snapshot",
            "kind": "cron",
            "hour": settings.snapshot_hour,
            "minute": settings.snapshot_minute,
            "next_run_time": next_run("daily_snapshot"),
        },
        {
            "id": "backtest_worker",
            "kind": "interval",
            "interval_seconds": settings.backtest_worker_interval_seconds,
        },
        {
            "id": "startup_market_data_catchup",
            "kind": "date",
            "next_run_time": next_run("startup_market_data_catchup"),
        },
        {
            "id": "snapshot_bootstrap",
            "kind": "on_demand",
            "pending": pending,
            "next_run_time": _iso_sh(min(bootstrap_times)) if bootstrap_times else None,
        },
    ]


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
        track_job("snapshot_bootstrap", bootstrap_watchlist_snapshot),
        args=[stock_code],
        id=f"snapshot_bootstrap_{stock_code}",
        replace_existing=True,
    )


def cancel_snapshot_bootstrap(stock_code: str) -> None:
    """Drop a pending snapshot bootstrap job after a watchlist removal.

    No-op when the scheduler is absent or the job is already gone.
    """
    scheduler = get_scheduler()
    if scheduler is None:
        return
    try:
        scheduler.remove_job(f"snapshot_bootstrap_{stock_code}")
    except JobLookupError:
        return
