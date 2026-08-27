from __future__ import annotations

from fastapi import APIRouter

from app.schemas.signal_strategy import (
    SignalStrategyClone,
    SignalStrategyCreate,
    SignalStrategyDefaultUpdate,
    SignalStrategyDryRun,
    SignalStrategyUpdate,
    WatchlistSignalStrategyUpdate,
)
from app.services.signal_strategy_service import (
    archive_strategy,
    assign_watchlist_strategy,
    clone_strategy,
    create_strategy,
    dry_run,
    get_strategy,
    list_operators,
    list_strategies,
    set_default_strategy,
    update_strategy,
)

router = APIRouter(prefix="/api/signal-strategies", tags=["signal-strategies"])


@router.get("/operators")
def read_operators() -> list[dict]:
    return list_operators()


@router.put("/default")
def put_default(payload: SignalStrategyDefaultUpdate) -> dict:
    return set_default_strategy(payload.strategy_id)


@router.post("/dry-run")
def post_dry_run(payload: SignalStrategyDryRun) -> dict:
    return dry_run(
        payload.stock_code,
        recipe=payload.recipe,
        strategy_id=payload.strategy_id,
        as_of=payload.as_of,
        policy=payload.policy,
    )


@router.put("/watchlist/{stock_code}")
def put_watchlist_strategy(
    stock_code: str,
    payload: WatchlistSignalStrategyUpdate,
) -> dict:
    return assign_watchlist_strategy(stock_code, payload.strategy_id)


@router.get("")
def read_strategies(include_archived: bool = False) -> list[dict]:
    return list_strategies(include_archived=include_archived)


@router.post("", status_code=201)
def post_strategy(payload: SignalStrategyCreate) -> dict:
    return create_strategy(payload.name, payload.description, payload.recipe)


@router.get("/{strategy_id}")
def read_strategy(strategy_id: int) -> dict:
    return get_strategy(strategy_id)


@router.put("/{strategy_id}")
def put_strategy(strategy_id: int, payload: SignalStrategyUpdate) -> dict:
    changes = payload.model_dump(
        exclude={"expected_version"},
        exclude_unset=True,
    )
    return update_strategy(
        strategy_id,
        expected_version=payload.expected_version,
        **changes,
    )


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int) -> dict:
    return archive_strategy(strategy_id)


@router.post("/{strategy_id}/clone", status_code=201)
def post_clone(strategy_id: int, payload: SignalStrategyClone) -> dict:
    return clone_strategy(strategy_id, payload.name)
