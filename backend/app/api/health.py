import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.config import settings
from app.engine.resilience import get_system_status

router = APIRouter(prefix="/api", tags=["health"])

SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 30.0


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
    }


@router.get("/system/status")
def system_status() -> dict:
    """Resilience flags for frontend (quote delay / consecutive poll failures)."""
    return get_system_status()


async def system_status_event_generator(
    *,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
):
    """Yield SSE payloads when system status changes; heartbeat comments otherwise."""
    last: dict | None = None
    last_heartbeat = time.monotonic()
    while True:
        current = get_system_status()
        if current != last:
            yield f"data: {json.dumps(current, ensure_ascii=False)}\n\n"
            last = current
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            yield ": ping\n\n"
            last_heartbeat = now
        await asyncio.sleep(poll_seconds)


@router.get("/system/status/stream")
async def system_status_stream():
    """Push system status when it changes; wait (with heartbeat) otherwise."""
    return StreamingResponse(
        system_status_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
