from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.triggers.date import DateTrigger

from app.config import settings
from app.engine.scheduler import (
    _on_job_error,
    cancel_snapshot_bootstrap,
    create_scheduler,
    list_activity_jobs,
)


def test_scheduler_contains_market_polling_job() -> None:
    scheduler = create_scheduler()
    job = scheduler.get_job("market_polling")
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.next_run_time is not None
    assert job.misfire_grace_time is None


def test_scheduler_contains_daily_snapshot_job() -> None:
    scheduler = create_scheduler()
    job = scheduler.get_job("daily_snapshot")
    assert job is not None
    assert job.trigger is not None


def test_scheduler_does_not_schedule_intraday_snapshot_job() -> None:
    scheduler = create_scheduler()
    assert scheduler.get_job("intraday_snapshot") is None
    assert scheduler.get_job("market_polling") is not None


def test_scheduler_contains_startup_catchup_job() -> None:
    scheduler = create_scheduler()
    job = scheduler.get_job("startup_market_data_catchup")
    assert job is not None
    assert isinstance(job.trigger, DateTrigger)
    assert job.trigger.run_date is not None
    assert job.misfire_grace_time is None


def test_scheduler_registers_activity_error_listener() -> None:
    scheduler = create_scheduler()
    assert any(
        callback is _on_job_error and mask == EVENT_JOB_ERROR
        for callback, mask in scheduler._listeners
    )


def test_list_activity_jobs_matches_scheduler_definitions() -> None:
    create_scheduler()
    views = {item["id"]: item for item in list_activity_jobs()}
    assert "intraday_snapshot" not in views
    assert views["market_polling"]["kind"] == "interval"
    assert views["market_polling"]["interval_seconds"] == settings.polling_interval_seconds
    assert views["market_polling"]["note"] == "含盘中快照"
    assert views["baseline_precompute"]["kind"] == "cron"
    assert views["baseline_precompute"]["hour"] == settings.baseline_hour
    assert views["baseline_precompute"]["minute"] == settings.baseline_minute
    assert views["daily_snapshot"]["kind"] == "cron"
    assert views["daily_snapshot"]["hour"] == settings.snapshot_hour
    assert views["backtest_worker"]["kind"] == "interval"
    assert (
        views["backtest_worker"]["interval_seconds"]
        == settings.backtest_worker_interval_seconds
    )
    assert views["startup_market_data_catchup"]["kind"] == "date"
    assert views["snapshot_bootstrap"]["kind"] == "on_demand"
    assert views["snapshot_bootstrap"]["pending"] == []


def test_list_activity_jobs_includes_pending_bootstrap() -> None:
    scheduler = create_scheduler()
    scheduler.add_job(
        lambda: None,
        id="snapshot_bootstrap_sh600519",
        replace_existing=True,
    )
    views = {item["id"]: item for item in list_activity_jobs()}
    assert views["snapshot_bootstrap"]["pending"] == ["sh600519"]


def test_cancel_snapshot_bootstrap_removes_pending_job() -> None:
    scheduler = create_scheduler()
    scheduler.add_job(
        lambda: None,
        id="snapshot_bootstrap_sh600519",
        replace_existing=True,
    )
    cancel_snapshot_bootstrap("sh600519")
    views = {item["id"]: item for item in list_activity_jobs()}
    assert views["snapshot_bootstrap"]["pending"] == []
    assert scheduler.get_job("snapshot_bootstrap_sh600519") is None


def test_cancel_snapshot_bootstrap_missing_job_is_noop() -> None:
    create_scheduler()
    cancel_snapshot_bootstrap("sh600519")
    views = {item["id"]: item for item in list_activity_jobs()}
    assert views["snapshot_bootstrap"]["pending"] == []
