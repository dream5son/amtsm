from fastapi import APIRouter

from app.schemas.strategy import StrategyConfig
from app.services.strategy_service import get_strategy, update_strategy

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("")
def read_strategy() -> dict:
    return get_strategy()


@router.put("")
def put_strategy(payload: StrategyConfig) -> dict:
    return update_strategy(payload)
