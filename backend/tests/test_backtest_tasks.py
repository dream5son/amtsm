import json

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import BacktestJob
from app.engine.activity_log import (
    install_activity_logging,
    recent,
    reset_activity_log,
    track_job,
)
from app.engine.backtest_tasks import backtest_worker_task


def _run_worker() -> None:
    reset_activity_log()
    install_activity_logging()
    track_job("backtest_worker", backtest_worker_task)()


def test_idle_does_not_emit_activity_log(tmp_path, monkeypatch) -> None:
    """Idle ticks stay at DEBUG so overnight SSE panels are not flooded."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    _run_worker()

    messages = [item["message"] for item in recent("backtest_worker")]
    assert messages == []


def test_pending_job_emits_started_and_finished(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        job = BacktestJob(
            stock_code="sh600519",
            status="PENDING",
            start_date="2020-01-01",
            end_date="2024-01-01",
            params_json=json.dumps({"n": 60, "x": 1.1}),
            params_hash="h",
            compare_group_id="g",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def fake_run_job(pending_id: int) -> None:
        with get_db() as session:
            row = session.get(BacktestJob, pending_id)
            assert row is not None
            row.status = "SUCCESS"
            row.win_rate = 0.5
            row.trade_count = 12
            session.commit()

    monkeypatch.setattr("app.engine.backtest_tasks.run_job", fake_run_job)
    _run_worker()

    messages = [item["message"] for item in recent("backtest_worker")]
    assert any(f"backtest_worker started job_id={job_id}" in msg for msg in messages)
    assert any(
        f"backtest_worker finished job_id={job_id} status=SUCCESS" in msg
        and "stock=sh600519" in msg
        and "win_rate=0.5" in msg
        and "trade_count=12" in msg
        for msg in messages
    )
    assert not any("idle" in msg for msg in messages)


def test_failed_job_emits_error_finished(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        job = BacktestJob(
            stock_code="sh600519",
            status="PENDING",
            start_date="2020-01-01",
            end_date="2024-01-01",
            params_json=json.dumps({"n": 60, "x": 1.1}),
            params_hash="h",
            compare_group_id="g",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def fake_run_job(pending_id: int) -> None:
        with get_db() as session:
            row = session.get(BacktestJob, pending_id)
            assert row is not None
            row.status = "FAILED"
            row.error_message = "no cached market data"
            session.commit()

    monkeypatch.setattr("app.engine.backtest_tasks.run_job", fake_run_job)
    _run_worker()

    items = recent("backtest_worker")
    finished = [item for item in items if "status=FAILED" in item["message"]]
    assert finished
    assert finished[-1]["level"] == "error"
    assert finished[-1]["job"] == "backtest_worker"
    assert f"job_id={job_id}" in finished[-1]["message"]
    assert "no cached market data" in finished[-1]["message"]
