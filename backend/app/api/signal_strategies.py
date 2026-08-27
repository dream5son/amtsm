from __future__ import annotations

from fastapi import APIRouter, HTTPException

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


def _raise_service_error(exc: ValueError) -> None:
    detail = str(exc)
    if "not found" in detail or "not configured" in detail:
        status_code = 404
    elif (
        "conflict" in detail
        or "already exists" in detail
        or "immutable" in detail
        or "cannot be archived" in detail
    ):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/operators")
def read_operators() -> list[dict]:
    return list_operators()


@router.put("/default")
def put_default(payload: SignalStrategyDefaultUpdate) -> dict:
    try:
        return set_default_strategy(payload.strategy_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/dry-run")
def post_dry_run(payload: SignalStrategyDryRun) -> dict:
    try:
        return dry_run(
            payload.stock_code,
            recipe=payload.recipe,
            strategy_id=payload.strategy_id,
            as_of=payload.as_of,
            policy=payload.policy,
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.put("/watchlist/{stock_code}")
def put_watchlist_strategy(
    stock_code: str,
    payload: WatchlistSignalStrategyUpdate,
) -> dict:
    try:
        return assign_watchlist_strategy(stock_code, payload.strategy_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("")
def read_strategies(include_archived: bool = False) -> list[dict]:
    return list_strategies(include_archived=include_archived)


@router.post("", status_code=201)
def post_strategy(payload: SignalStrategyCreate) -> dict:
    try:
        return create_strategy(payload.name, payload.description, payload.recipe)
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/{strategy_id}")
def read_strategy(strategy_id: int) -> dict:
    try:
        return get_strategy(strategy_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.put("/{strategy_id}")
def put_strategy(strategy_id: int, payload: SignalStrategyUpdate) -> dict:
    changes = payload.model_dump(
        exclude={"expected_version"},
        exclude_unset=True,
    )
    try:
        return update_strategy(
            strategy_id,
            expected_version=payload.expected_version,
            **changes,
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int) -> dict:
    try:
        return archive_strategy(strategy_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/{strategy_id}/clone", status_code=201)
def post_clone(strategy_id: int, payload: SignalStrategyClone) -> dict:
    try:
        return clone_strategy(strategy_id, payload.name)
    except ValueError as exc:
        _raise_service_error(exc)
