import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.notify import router as notify_router
from app.api.stocks import router as stocks_router
from app.api.strategy import router as strategy_router
from app.api.watchlist import router as watchlist_router
from app.config import settings
from app.db.init_db import init_db
from app.engine.scheduler import create_scheduler
from app.services.wechat_notifier import wechat_notifier

logger = logging.getLogger(__name__)
scheduler = create_scheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # WeChat self-check must never crash process startup (user story 07 NFR).
    if settings.wechat_self_check_on_startup:
        try:
            wechat_notifier.self_check()
        except Exception:
            # Defensive: notifier already catches send failures; keep startup alive.
            logger.exception(
                "WeChat channel self-check raised unexpectedly; continuing startup"
            )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="AMTSM API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(notify_router)
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
