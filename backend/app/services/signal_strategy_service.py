from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db.connection import get_db
from app.db.models import DailyMarketSnapshot, SignalStrategy, StrategyConfig, Watchlist
from app.market_signal.compiler import (
    EvaluationPolicy,
    analyze_recipe,
    dry_run_recipe,
)
from app.market_signal.feed import BarWindowFeed
from app.market_signal.operators.registry import DEFAULT_OPERATOR_REGISTRY
from app.market_signal.recipe_schema import (
    RecipeInput,
    StrategyRecipeV1,
    hash_recipe,
    normalize,
    validate_recipe,
)
from app.market_signal.strategy import SignalRequest
from app.services.market_data import bar_date_str
from app.services.market_data_service import fetch_daily_bars
from app.services.stock_search_service import normalize_stock_code

_UNSET = object()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _recipe_json(recipe: RecipeInput) -> str:
    return json.dumps(
        normalize(recipe),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strategy_dict(row: SignalStrategy) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "builtin_key": row.builtin_key,
        "recipe": json.loads(row.recipe_json),
        "recipe_schema_version": row.recipe_schema_version,
        "recipe_version": row.recipe_version,
        "recipe_hash": row.recipe_hash,
        "is_archived": bool(row.is_archived),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _snapshot(row: SignalStrategy) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "version": row.recipe_version,
        "hash": row.recipe_hash,
        "recipe": json.loads(row.recipe_json),
        "builtin_key": row.builtin_key,
    }


def _validated_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("name is required")
    if len(normalized) > 100:
        raise ValueError("name must be at most 100 characters")
    return normalized


def list_operators() -> list[dict[str, Any]]:
    return [
        {
            "op": spec.op,
            "version": spec.version,
            "channels": sorted(spec.channels),
            "params_schema": spec.params_model.model_json_schema(),
        }
        for spec in DEFAULT_OPERATOR_REGISTRY.values()
    ]


def list_strategies(include_archived: bool = False) -> list[dict[str, Any]]:
    with get_db() as session:
        config = session.get(StrategyConfig, 1)
        default_id = (
            config.default_signal_strategy_id if config is not None else None
        )
        query = session.query(SignalStrategy)
        if not include_archived:
            query = query.filter(SignalStrategy.is_archived == 0)
        rows = query.order_by(SignalStrategy.builtin_key.desc(), SignalStrategy.id).all()
        return [
            {**_strategy_dict(row), "is_default": row.id == default_id}
            for row in rows
        ]


def get_strategy(strategy_id: int) -> dict[str, Any]:
    with get_db() as session:
        row = session.get(SignalStrategy, strategy_id)
        if row is None:
            raise ValueError("signal strategy not found")
        return _strategy_dict(row)


def create_strategy(
    name: str,
    description: str | None,
    recipe: RecipeInput,
) -> dict[str, Any]:
    validated = validate_recipe(recipe)
    row = SignalStrategy(
        name=_validated_name(name),
        description=description,
        recipe_json=_recipe_json(validated),
        recipe_schema_version=validated.schema_version,
        recipe_version=1,
        recipe_hash=hash_recipe(validated),
        is_archived=0,
    )
    with get_db() as session:
        try:
            session.add(row)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("signal strategy name already exists") from exc
        return _strategy_dict(row)


def update_strategy(
    strategy_id: int,
    *,
    expected_version: int,
    name: str | object = _UNSET,
    description: str | None | object = _UNSET,
    recipe: RecipeInput | object = _UNSET,
) -> dict[str, Any]:
    with get_db() as session:
        row = session.get(SignalStrategy, strategy_id)
        if row is None:
            raise ValueError("signal strategy not found")
        if row.builtin_key is not None:
            raise ValueError("builtin signal strategies are immutable")
        if row.recipe_version != expected_version:
            raise ValueError("signal strategy version conflict")
        if name is not _UNSET:
            if not isinstance(name, str):
                raise ValueError("name is required")
            row.name = _validated_name(name)
        if description is not _UNSET:
            row.description = description
        if recipe is not _UNSET:
            validated = validate_recipe(recipe)
            row.recipe_json = _recipe_json(validated)
            row.recipe_schema_version = validated.schema_version
            row.recipe_hash = hash_recipe(validated)
        row.recipe_version += 1
        row.updated_at = _utcnow()
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("signal strategy name already exists") from exc
        return _strategy_dict(row)


def archive_strategy(strategy_id: int) -> dict[str, Any]:
    with get_db() as session:
        row = session.get(SignalStrategy, strategy_id)
        if row is None:
            raise ValueError("signal strategy not found")
        if row.builtin_key is not None:
            raise ValueError("builtin signal strategies cannot be archived")
        if (
            session.query(Watchlist)
            .filter(Watchlist.signal_strategy_id == strategy_id)
            .first()
            is not None
        ):
            raise ValueError("assigned signal strategies cannot be archived")
        config = session.get(StrategyConfig, 1)
        if config is not None and config.default_signal_strategy_id == strategy_id:
            raise ValueError("default signal strategy cannot be archived")
        row.is_archived = 1
        row.updated_at = _utcnow()
        session.commit()
        return _strategy_dict(row)


def clone_strategy(strategy_id: int, name: str) -> dict[str, Any]:
    with get_db() as session:
        row = session.get(SignalStrategy, strategy_id)
        if row is None:
            raise ValueError("signal strategy not found")
        description = row.description
        recipe = row.recipe_json
    return create_strategy(name, description, recipe)


def set_default_strategy(strategy_id: int) -> dict[str, Any]:
    with get_db() as session:
        strategy = session.get(SignalStrategy, strategy_id)
        if strategy is None or strategy.is_archived:
            raise ValueError("active signal strategy not found")
        config = session.get(StrategyConfig, 1)
        if config is None:
            raise ValueError("strategy config not found")
        config.default_signal_strategy_id = strategy_id
        config.updated_at = _utcnow()
        session.commit()
        return _snapshot(strategy)


def assign_watchlist_strategy(
    stock_code: str,
    strategy_id: int | None,
) -> dict[str, Any]:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        watchlist = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized)
            .one_or_none()
        )
        if watchlist is None:
            raise ValueError("stock not found in watchlist")
        if strategy_id is not None:
            strategy = session.get(SignalStrategy, strategy_id)
            if strategy is None or strategy.is_archived:
                raise ValueError("active signal strategy not found")
        watchlist.signal_strategy_id = strategy_id
        session.commit()
    return resolve_signal_strategy(normalized)


def resolve_signal_strategy(stock_code: str) -> dict[str, Any]:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        watchlist = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized)
            .one_or_none()
        )
        if watchlist is None:
            raise ValueError("stock not found in watchlist")
        config = session.get(StrategyConfig, 1)
        strategy_id = watchlist.signal_strategy_id or (
            config.default_signal_strategy_id if config is not None else None
        )
        strategy = (
            session.get(SignalStrategy, strategy_id)
            if strategy_id is not None
            else None
        )
        if strategy is None:
            raise ValueError("default signal strategy is not configured")
        return _snapshot(strategy)


def resolve_signal_strategies(
    stock_codes: list[str],
) -> dict[str, dict[str, Any]]:
    normalized_codes = [normalize_stock_code(code) for code in stock_codes]
    if not normalized_codes:
        return {}
    with get_db() as session:
        watchlists = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code.in_(normalized_codes))
            .all()
        )
        by_code = {row.stock_code: row for row in watchlists}
        missing = [code for code in normalized_codes if code not in by_code]
        if missing:
            raise ValueError(f"stock not found in watchlist: {missing[0]}")
        config = session.get(StrategyConfig, 1)
        default_id = config.default_signal_strategy_id if config is not None else None
        strategy_ids = {
            row.signal_strategy_id or default_id
            for row in watchlists
            if row.signal_strategy_id or default_id
        }
        strategies = {
            row.id: row
            for row in session.query(SignalStrategy)
            .filter(SignalStrategy.id.in_(strategy_ids))
            .all()
        }
        resolved: dict[str, dict[str, Any]] = {}
        for code in normalized_codes:
            strategy_id = by_code[code].signal_strategy_id or default_id
            strategy = strategies.get(strategy_id)
            if strategy is None:
                raise ValueError("default signal strategy is not configured")
            resolved[code] = _snapshot(strategy)
        return resolved


def _snapshot_bar(row: DailyMarketSnapshot) -> dict[str, Any]:
    return {
        "date": row.trade_date,
        "open": float(row.open_price),
        "high": float(row.high_price),
        "low": float(row.low_price),
        "close": float(row.close_price),
        "volume": float(row.volume),
        "turnover_rate": row.turnover_rate,
    }


def _load_bars(
    stock_code: str,
    *,
    as_of: date | None,
    history_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with get_db() as session:
        query = session.query(DailyMarketSnapshot).filter(
            DailyMarketSnapshot.stock_code == stock_code
        )
        if as_of is not None:
            query = query.filter(DailyMarketSnapshot.trade_date <= as_of.isoformat())
        cached_rows = (
            query.order_by(DailyMarketSnapshot.trade_date.desc())
            .limit(history_count + 1)
            .all()
        )
    cached = [_snapshot_bar(row) for row in reversed(cached_rows)]
    bars_by_date = {bar["date"]: bar for bar in cached}

    if len(cached) < history_count + 1:
        fetch_end = (as_of or datetime.now(UTC).date()) + timedelta(days=1)
        try:
            fetched = fetch_daily_bars(stock_code, history_count + 1, fetch_end)
        except Exception:
            if not cached:
                raise
        else:
            for bar in fetched:
                day = bar_date_str(bar.get("date"))
                if day and (as_of is None or day <= as_of.isoformat()):
                    bars_by_date[day] = {**bar, "date": day}

    ordered = [bars_by_date[day] for day in sorted(bars_by_date)]
    if not ordered:
        raise ValueError("no market snapshots available for dry run")
    current = ordered[-1]
    history = ordered[:-1][-history_count:]
    return current, history


def _trace_dict(trace) -> dict[str, Any]:
    return {
        "present": trace.present,
        "passed": trace.passed,
        "gates_passed": trace.gates_passed,
        "policy_passed": trace.policy_passed,
        "gates": [
            {
                "channel": gate.channel,
                "index": gate.index,
                "op": gate.op,
                "version": gate.version,
                "params": dict(gate.params),
                "passed": gate.passed,
                "details": dict(gate.details),
            }
            for gate in trace.gates
        ],
    }


def dry_run(
    stock_code: str,
    *,
    recipe: RecipeInput | None = None,
    strategy_id: int | None = None,
    as_of: date | None = None,
    policy: str | EvaluationPolicy = "live",
) -> dict[str, Any]:
    if (recipe is None) == (strategy_id is None):
        raise ValueError("provide exactly one of recipe or strategy_id")
    normalized = normalize_stock_code(stock_code)
    strategy_snapshot = None
    if strategy_id is not None:
        strategy_snapshot = get_strategy(strategy_id)
        if strategy_snapshot["is_archived"]:
            raise ValueError("active signal strategy not found")
        validated: StrategyRecipeV1 = validate_recipe(strategy_snapshot["recipe"])
    else:
        validated = validate_recipe(recipe)

    if isinstance(policy, str):
        if policy != "live":
            raise ValueError("unsupported evaluation policy")
        evaluation_policy = EvaluationPolicy(require_full_buy_window=False)
    else:
        evaluation_policy = policy

    requirements = analyze_recipe(validated)
    history_count = max(
        requirements.price_lookback_days,
        requirements.volume_lookback_days or 0,
    )
    current, history = _load_bars(
        normalized,
        as_of=as_of,
        history_count=history_count,
    )
    request = SignalRequest(
        price=float(current["close"]),
        volume=(
            None if current.get("volume") is None else float(current["volume"])
        ),
        feed=BarWindowFeed(
            history,
            requested_n=requirements.price_lookback_days,
        ),
    )
    result = dry_run_recipe(validated, request, policy=evaluation_policy)
    return {
        "stock_code": normalized,
        "as_of": current["date"],
        "strategy": (
            None
            if strategy_snapshot is None
            else {
                "id": strategy_snapshot["id"],
                "name": strategy_snapshot["name"],
                "version": strategy_snapshot["recipe_version"],
                "hash": strategy_snapshot["recipe_hash"],
                "builtin_key": strategy_snapshot["builtin_key"],
            }
        ),
        "recipe_hash": hash_recipe(validated),
        "decision": asdict(result.decision),
        "traces": {
            channel: _trace_dict(trace)
            for channel, trace in result.traces.items()
        },
        "requirements": asdict(requirements),
    }
