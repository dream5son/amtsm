import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.db.baseline_job_log_repo import get_job_log_by_id, get_latest_job_log, get_log_items
from app.engine.state import runtime_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs/daily-baseline", tags=["jobs"])

_STATUS_MAP = {
    "IDLE": "未开始",
    "RUNNING": "执行中",
    "FAILED": "执行失败",
    "SUCCESS": "执行成功",
}


@router.get("/status")
def get_status():
    status_label = _STATUS_MAP.get(runtime_state.job_status, "未开始")
    latest = get_latest_job_log()
    return {"status": status_label, "latest_job": latest}


@router.get("/status/stream")
async def status_stream():
    async def event_generator():
        last_status = None
        while True:
            current = runtime_state.job_status
            if current != last_status:
                label = _STATUS_MAP.get(current, "未开始")
                yield f"data: {label}\n\n"
                last_status = current
            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/logs/latest")
def get_latest_log(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    job_log = get_latest_job_log()
    if not job_log:
        return {"job_log": None, "items": []}
    items = get_log_items(job_log["id"], page, size)
    return {"job_log": job_log, "items": items}


@router.get("/logs/{job_log_id}")
def get_log_detail(job_log_id: int, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    job_log = get_job_log_by_id(job_log_id)
    if not job_log:
        return {"job_log": None, "items": []}
    items = get_log_items(job_log_id, page, size)
    return {"job_log": job_log, "items": items}
