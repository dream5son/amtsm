from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.stocks import router as stocks_router
from app.api.strategy import router as strategy_router
from app.api.watchlist import router as watchlist_router
from app.config import settings
from app.db.init_db import init_db
from app.engine.scheduler import create_scheduler

scheduler = create_scheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
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
