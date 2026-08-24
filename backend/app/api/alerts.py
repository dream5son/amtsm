from fastapi import APIRouter, HTTPException, Query

from app.services.alert_service import list_alerts_by_stock

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/{stock_code}")
def get_alerts(
    stock_code: str,
    limit: int = Query(default=20),
    offset: int = Query(default=0),
) -> dict:
    try:
        return list_alerts_by_stock(stock_code, limit=limit, offset=offset)
    except ValueError as exc:
        detail = str(exc)
        if detail == "stock not found in watchlist":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
