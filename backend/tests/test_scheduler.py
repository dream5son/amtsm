from apscheduler.triggers.date import DateTrigger

from app.engine.scheduler import create_scheduler


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


def test_scheduler_contains_intraday_snapshot_job() -> None:
    scheduler = create_scheduler()
    job = scheduler.get_job("intraday_snapshot")
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.next_run_time is not None
    assert job.misfire_grace_time is None


def test_scheduler_contains_startup_catchup_job() -> None:
    scheduler = create_scheduler()
    job = scheduler.get_job("startup_market_data_catchup")
    assert job is not None
    assert isinstance(job.trigger, DateTrigger)
    assert job.trigger.run_date is not None
    assert job.misfire_grace_time is None
