"""Async backtest worker: polls for PENDING backtest_jobs and runs them serially.

One job at a time keeps writes to the single-writer SQLite database well
behaved (design.md 关键点5: "任务表 + 引擎侧 Worker").
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.connection import get_db
from app.db.models import BacktestJob
from app.services.backtest_service import run_job

logger = logging.getLogger(__name__)


def _log_job_finished(job_id: int) -> None:
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
    if job is None:
        logger.info("backtest_worker finished job_id=%s status=missing", job_id)
        return
    if job.status == "SUCCESS":
        logger.info(
            "backtest_worker finished job_id=%s status=SUCCESS stock=%s "
            "range=%s..%s win_rate=%s trade_count=%s",
            job.id,
            job.stock_code,
            job.start_date,
            job.end_date,
            job.win_rate,
            job.trade_count,
        )
        return
    if job.status == "FAILED":
        logger.error(
            "backtest_worker finished job_id=%s status=FAILED stock=%s error=%s",
            job.id,
            job.stock_code,
            job.error_message,
        )
        return
    logger.info(
        "backtest_worker finished job_id=%s status=%s stock=%s",
        job.id,
        job.status,
        job.stock_code,
    )


def backtest_worker_task() -> None:
    with get_db() as session:
        job_id = session.execute(
            select(BacktestJob.id)
            .where(BacktestJob.status == "PENDING")
            .order_by(BacktestJob.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    if job_id is None:
        # Debug only: INFO would hit the activity SSE panel every few seconds
        # when idle and amplify CPU under leaked long-lived connections.
        logger.debug("backtest_worker idle: no PENDING jobs")
        return

    logger.info("backtest_worker started job_id=%s", job_id)
    try:
        run_job(job_id)
    except Exception:
        logger.exception("backtest_worker_task: unexpected error running job_id=%s", job_id)
        return

    _log_job_finished(job_id)
