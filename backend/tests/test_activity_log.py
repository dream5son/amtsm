import logging

from app.engine.activity_log import (
    BUFFER_MAXLEN,
    classify_level,
    emit,
    install_activity_logging,
    map_scheduler_job_id,
    recent,
    reset_activity_log,
    since,
    snapshot,
    track_job,
)


def setup_function() -> None:
    reset_activity_log()
    install_activity_logging()


def test_map_dynamic_bootstrap_job_id() -> None:
    assert map_scheduler_job_id("snapshot_bootstrap_sh600519") == "snapshot_bootstrap"
    assert map_scheduler_job_id("market_polling") == "market_polling"
    assert map_scheduler_job_id("unknown_job") is None


def test_buckets_do_not_evict_each_other() -> None:
    for index in range(BUFFER_MAXLEN + 8):
        emit("info", f"poll {index}", job="market_polling")
    emit("info", "baseline done", job="baseline_precompute")

    polling = recent("market_polling", limit=BUFFER_MAXLEN)
    assert len(polling) == BUFFER_MAXLEN
    assert polling[0]["message"] == "poll 8"
    assert recent("baseline_precompute")[-1]["message"] == "baseline done"


def test_recent_returns_last_n() -> None:
    for index in range(25):
        emit("info", f"row {index}", job="daily_snapshot")
    items = recent("daily_snapshot", limit=20)
    assert len(items) == 20
    assert items[0]["message"] == "row 5"
    assert items[-1]["message"] == "row 24"


def test_handler_classifies_info_error_notify() -> None:
    tasks = logging.getLogger("app.engine.tasks")
    alerts = logging.getLogger("app.services.alert_service")

    tasks.info("market_polling_task summary trade_date=2024-01-01")
    tasks.error("daily_snapshot_task aborted: market data unavailable")
    alerts.info("buy_alert SENT stock=sh600519 date=2024-01-01 price=10 latency_ms=1.0")

    polling = recent("market_polling")
    snapshot_items = recent("daily_snapshot")
    assert polling[0]["level"] == "info"
    assert snapshot_items[0]["level"] == "error"
    assert polling[-1]["level"] == "notify"
    assert "SENT" in polling[-1]["message"]


def test_intraday_snapshot_logs_file_under_polling() -> None:
    logging.getLogger("app.engine.tasks").info(
        "intraday_snapshot_task finished trade_date=2024-01-01 total=1 success=1 skipped=0"
    )
    items = recent("market_polling")
    assert items[-1]["job"] == "market_polling"
    assert "intraday_snapshot_task finished" in items[-1]["message"]


def test_debug_logs_are_ignored() -> None:
    logging.getLogger("app.engine.tasks").debug(
        "market_polling_task skipped: not trading day (2024-01-01)"
    )
    assert recent("market_polling") == []


def test_startup_catchup_not_filed_as_baseline() -> None:
    logging.getLogger("app.engine.tasks").info(
        "startup_market_data_catchup running missed baseline_precompute for 2024-01-01"
    )
    assert recent("startup_market_data_catchup")
    assert recent("baseline_precompute") == []


def test_track_job_files_unprefixed_logs() -> None:
    log = logging.getLogger("app.engine.tasks")

    def job() -> None:
        log.info("halt_resume_candidate stock=sh600519 trade_date=2024-01-01 actual_n=60")

    track_job("baseline_precompute", job)()
    items = recent("baseline_precompute")
    assert items[-1]["job"] == "baseline_precompute"
    assert "halt_resume_candidate" in items[-1]["message"]


def test_classify_notify_requires_sent_marker() -> None:
    assert (
        classify_level(
            logging.INFO,
            "buy_alert SENT stock=sh600519",
            logger_name="app.services.alert_service",
        )
        == "notify"
    )
    assert (
        classify_level(
            logging.ERROR,
            "buy_alert FAILED stock=sh600519",
            logger_name="app.services.alert_service",
        )
        == "error"
    )


def test_since_and_snapshot() -> None:
    emit("info", "a", job="market_polling")
    first_id = recent("market_polling")[0]["id"]
    emit("error", "b", job="backtest_worker")
    newer = since(first_id)
    assert len(newer) == 1
    assert newer[0]["job"] == "backtest_worker"
    jobs = snapshot(limit=20)
    assert set(jobs) >= {"market_polling", "backtest_worker"}
    assert jobs["market_polling"][0]["message"] == "a"


def test_backtest_service_logs_file_under_worker() -> None:
    logging.getLogger("app.services.backtest_service").warning(
        "backtest job 1 failed: no cached market data"
    )
    items = recent("backtest_worker")
    assert items[-1]["job"] == "backtest_worker"
    assert "backtest job 1 failed" in items[-1]["message"]


def test_track_job_files_backtest_service_logs() -> None:
    log = logging.getLogger("app.services.backtest_service")

    def job() -> None:
        log.info("backtest job 9 started stock=sh600519 range=2020-01-01..2024-01-01 n=60 x=1.1")

    track_job("backtest_worker", job)()
    items = recent("backtest_worker")
    assert items[-1]["job"] == "backtest_worker"
    assert "backtest job 9 started" in items[-1]["message"]
