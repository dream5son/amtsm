from fastapi import APIRouter, HTTPException, Query

from app.schemas.position import PositionTradeRequest
from app.services.position_service import (
    StockNotFoundError,
    list_ledgers,
    preview_buy,
    register_buy,
    register_sell,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("/{stock_code}/preview-buy")
def get_preview_buy(
    stock_code: str,
    qty: int = Query(gt=0),
    price: float = Query(gt=0),
) -> dict:
    try:
        return preview_buy(stock_code, qty=qty, price=price)
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{stock_code}/buys", status_code=201)
def post_buy(stock_code: str, payload: PositionTradeRequest) -> dict:
    try:
        return register_buy(stock_code, payload)
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{stock_code}/sells", status_code=201)
def post_sell(stock_code: str, payload: PositionTradeRequest) -> dict:
    try:
        return register_sell(stock_code, payload)
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{stock_code}/ledgers")
def get_ledgers(stock_code: str) -> list[dict]:
    try:
        return list_ledgers(stock_code)
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
