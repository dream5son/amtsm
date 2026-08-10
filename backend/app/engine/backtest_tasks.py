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


def backtest_worker_task() -> None:
    with get_db() as session:
        job_id = session.execute(
            select(BacktestJob.id)
            .where(BacktestJob.status == "PENDING")
            .order_by(BacktestJob.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    if job_id is None:
        return

    try:
        run_job(job_id)
    except Exception:  # noqa: BLE001 - run_job already isolates failures per job
        logger.exception("backtest_worker_task: unexpected error running job_id=%s", job_id)
