from app.engine.scheduler import create_scheduler


def test_scheduler_contains_market_polling_job() -> None:
    scheduler = create_scheduler()
    try:
        job = scheduler.get_job("market_polling")
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True
    finally:
        scheduler.shutdown(wait=False)
