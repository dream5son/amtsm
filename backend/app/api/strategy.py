from fastapi import APIRouter, HTTPException

from app.schemas.strategy import StockStrategyOverride, StrategyConfig
from app.services.strategy_service import (
    clear_override,
    get_override,
    get_strategy,
    update_override,
    update_strategy,
)

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("")
def read_strategy() -> dict:
    return get_strategy()


@router.put("")
def put_strategy(payload: StrategyConfig) -> dict:
    return update_strategy(payload)


@router.get("/overrides/{stock_code}")
def read_override(stock_code: str) -> dict:
    try:
        return get_override(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/overrides/{stock_code}")
def put_override(stock_code: str, payload: StockStrategyOverride) -> dict:
    try:
        return update_override(stock_code, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/overrides/{stock_code}")
def delete_override(stock_code: str) -> dict:
    try:
        return clear_override(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
