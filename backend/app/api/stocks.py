from fastapi import APIRouter, Query

from app.schemas.stock import StockSearchItem
from app.services.stock_search_service import search_stocks

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/search", response_model=list[StockSearchItem])
def search_stock(
    q: str = Query(min_length=1, max_length=30),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    return search_stocks(q, limit)
