from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.schemas.stock import StockSearchItem
from app.services.market_data_service import fetch_qfq_bars_range
from app.services.stock_search_service import search_stocks

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/search", response_model=list[StockSearchItem])
def search_stock(
    q: str = Query(min_length=1, max_length=30),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    return search_stocks(q, limit)


@router.get("/{stock_code}/kline")
def get_stock_kline(
    stock_code: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=5000),
) -> list[dict]:
    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=365 * 3)
    bars = fetch_qfq_bars_range(stock_code, start, end)
    if limit is not None and len(bars) > limit:
        return bars[-limit:]
    return bars
