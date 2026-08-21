import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.engine.activity_log import (
    DEFAULT_LIMIT,
    JOB_IDS,
    max_id,
    recent,
    since,
    snapshot,
)
from app.engine.scheduler import list_activity_jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

SSE_POLL_SECONDS = 2.0
SSE_HEARTBEAT_SECONDS = 30.0
# Scheduler job metadata changes rarely; avoid get_jobs() on every poll tick.
SSE_SCHEDULER_SECONDS = 5.0
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/activity")
def get_activity(
    job: str | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
):
    """Recent scheduler activity logs, optionally filtered to one job bucket."""
    if job is None:
        return {"jobs": snapshot(limit), "scheduler": list_activity_jobs()}
    if job not in JOB_IDS:
        raise HTTPException(status_code=400, detail="unknown job")
    return {"job": job, "items": recent(job, limit)}


async def activity_event_generator(
    request: Request | None = None,
    *,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    scheduler_seconds: float = SSE_SCHEDULER_SECONDS,
    limit: int = DEFAULT_LIMIT,
):
    """Yield a per-job snapshot first, then incremental items as they appear.

    Exit when the client disconnects so leaked SSE connections cannot pile up.
    """
    scheduler_jobs = list_activity_jobs()
    yield (
        "data: "
        + json.dumps(
            {"type": "snapshot", "jobs": snapshot(limit), "scheduler": scheduler_jobs},
            ensure_ascii=False,
        )
        + "\n\n"
    )
    last_seen = max_id()
    last_heartbeat = time.monotonic()
    last_scheduler_check = time.monotonic()
    last_scheduler = scheduler_jobs
    while True:
        if request is not None and await request.is_disconnected():
            break
        new_items = since(last_seen)
        if new_items:
            yield f"data: {json.dumps({'type': 'delta', 'items': new_items}, ensure_ascii=False)}\n\n"
            last_seen = new_items[-1]["id"]
        now = time.monotonic()
        if now - last_scheduler_check >= scheduler_seconds:
            current_scheduler = list_activity_jobs()
            if current_scheduler != last_scheduler:
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "scheduler", "scheduler": current_scheduler},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                last_scheduler = current_scheduler
            last_scheduler_check = now
        if now - last_heartbeat >= heartbeat_seconds:
            yield ": ping\n\n"
            last_heartbeat = now
        await asyncio.sleep(poll_seconds)


@router.get("/activity/stream")
async def activity_stream(request: Request):
    """Push scheduler activity; wait (with heartbeat) otherwise."""
    return StreamingResponse(
        activity_event_generator(request),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
