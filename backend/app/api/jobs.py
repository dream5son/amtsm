import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.db.connection import get_db
from app.db.models import BaselineJobLog, BaselineJobLogItem
from app.engine.state import runtime_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs/daily-baseline", tags=["jobs"])

_STATUS_MAP = {
    "IDLE": "未开始",
    "RUNNING": "执行中",
    "FAILED": "执行失败",
    "SUCCESS": "执行成功",
}


def _job_log_to_dict(row: BaselineJobLog) -> dict:
    return {
        "id": row.id,
        "job_name": row.job_name,
        "trade_date": row.trade_date,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "total_count": row.total_count,
        "success_count": row.success_count,
        "failed_count": row.failed_count,
        "error_summary": row.error_summary,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _job_log_item_to_dict(row: BaselineJobLogItem) -> dict:
    return {
        "id": row.id,
        "job_log_id": row.job_log_id,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "strategy_n": row.strategy_n,
        "actual_n": row.actual_n,
        "status": row.status,
        "low_min": row.low_min,
        "high_max": row.high_max,
        "error_message": row.error_message,
        "processed_at": row.processed_at,
    }


def _get_latest_job_log() -> dict | None:
    with get_db() as session:
        row = session.scalars(
            select(BaselineJobLog).order_by(BaselineJobLog.id.desc()).limit(1)
        ).first()
        return _job_log_to_dict(row) if row else None


def _get_job_log_by_id(job_log_id: int) -> dict | None:
    with get_db() as session:
        row = session.get(BaselineJobLog, job_log_id)
        return _job_log_to_dict(row) if row else None


def _get_log_items(job_log_id: int, page: int = 1, size: int = 50) -> list[dict]:
    offset = (page - 1) * size
    with get_db() as session:
        rows = session.scalars(
            select(BaselineJobLogItem)
            .where(BaselineJobLogItem.job_log_id == job_log_id)
            .order_by(BaselineJobLogItem.id)
            .limit(size)
            .offset(offset)
        ).all()
        return [_job_log_item_to_dict(r) for r in rows]


@router.get("/status")
def get_status():
    status_label = _STATUS_MAP.get(runtime_state.job_status, "未开始")
    latest = _get_latest_job_log()
    return {"status": status_label, "latest_job": latest}


@router.get("/status/stream")
async def status_stream(request: Request):
    async def event_generator():
        last_status = None
        while True:
            if await request.is_disconnected():
                break
            current = runtime_state.job_status
            if current != last_status:
                label = _STATUS_MAP.get(current, "未开始")
                yield f"data: {label}\n\n"
                last_status = current
            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/logs/latest")
def get_latest_log(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    job_log = _get_latest_job_log()
    if not job_log:
        return {"job_log": None, "items": []}
    items = _get_log_items(job_log["id"], page, size)
    return {"job_log": job_log, "items": items}


@router.get("/logs/{job_log_id}")
def get_log_detail(job_log_id: int, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    job_log = _get_job_log_by_id(job_log_id)
    if not job_log:
        return {"job_log": None, "items": []}
    items = _get_log_items(job_log_id, page, size)
    return {"job_log": job_log, "items": items}
