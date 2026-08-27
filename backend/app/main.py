import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.activity import router as activity_router
from app.api.alerts import router as alerts_router
from app.api.backtests import router as backtests_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.notify import router as notify_router
from app.api.positions import router as positions_router
from app.api.signal_strategies import router as signal_strategies_router
from app.api.stocks import router as stocks_router
from app.api.strategy import router as strategy_router
from app.api.watchlist import router as watchlist_router
from app.config import settings
from app.db.init_db import init_db
from app.engine.scheduler import create_scheduler, get_scheduler
from app.services.market_data.baostock_socket import drop_baostock_socket
from app.services.errors import AppError
from app.services.notifier.registry import (
    get_enabled_notifiers,
    get_unknown_channel_ids,
)

logger = logging.getLogger(__name__)
scheduler = create_scheduler()


def _run_startup_self_checks() -> None:
    """Self-check enabled notify channels; never crash process startup."""
    for channel_id in get_unknown_channel_ids():
        logger.error("Unknown notify channel in NOTIFY_CHANNELS: %s", channel_id)

    for notifier in get_enabled_notifiers():
        if not notifier.self_check_on_startup:
            continue
        try:
            notifier.self_check()
        except Exception:
            logger.exception(
                "%s channel self-check raised unexpectedly; continuing startup",
                notifier.channel_id,
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _run_startup_self_checks()
    # Prefer module singleton (same instance as create_scheduler return).
    active = get_scheduler() or scheduler
    active.start()
    try:
        yield
    finally:
        active.shutdown(wait=False)
        drop_baostock_socket()


app = FastAPI(title="AMTSM API", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(activity_router)
app.include_router(alerts_router)
app.include_router(backtests_router)
app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(notify_router)
app.include_router(positions_router)
app.include_router(signal_strategies_router)
app.include_router(stocks_router)
app.include_router(watchlist_router)
app.include_router(strategy_router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
