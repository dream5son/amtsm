from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.connection import get_db
from app.db.models import Position, StockStrategyOverride, StrategyConfig, Watchlist
from app.schemas.strategy import (
    DEFAULT_TRAILING_LADDER_JSON,
    StockStrategyOverride as StockStrategyOverrideSchema,
    StrategyConfig as StrategyConfigSchema,
    ladder_to_json,
    parse_trailing_ladder,
)
from app.market_signal import evaluate, risk_params_from_resolved
from app.services.stock_search_service import normalize_stock_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _int_to_bool(value: int | None, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _row_to_strategy_dict(row: StrategyConfig) -> dict:
    ladder = parse_trailing_ladder(row.trailing_ladder_json or DEFAULT_TRAILING_LADDER_JSON)
    return {
        "global_buy_n": row.global_buy_n,
        "global_buy_x": row.global_buy_x,
        "global_sell_n": row.global_sell_n,
        "global_sell_y": row.global_sell_y,
        "stop_loss_pct": float(row.stop_loss_pct if row.stop_loss_pct is not None else 0.15),
        "break_even_trigger_pct": float(
            row.break_even_trigger_pct
            if getattr(row, "break_even_trigger_pct", None) is not None
            else 0.25
        ),
        "break_even_buffer_pct": float(
            row.break_even_buffer_pct
            if getattr(row, "break_even_buffer_pct", None) is not None
            else 0.005
        ),
        "trailing_ladder": [level.model_dump() for level in ladder],
        "enable_partial_take_profit": _int_to_bool(
            getattr(row, "enable_partial_take_profit", 0)
        ),
        "enable_addon_alert": _int_to_bool(getattr(row, "enable_addon_alert", 0)),
        "enable_tech_sell_while_holding": _int_to_bool(
            getattr(row, "enable_tech_sell_while_holding", 0)
        ),
    }


def _default_strategy_dict() -> dict:
    ladder = parse_trailing_ladder(DEFAULT_TRAILING_LADDER_JSON)
    return {
        "global_buy_n": 60,
        "global_buy_x": 1.1,
        "global_sell_n": 60,
        "global_sell_y": 0.9,
        "stop_loss_pct": 0.15,
        "break_even_trigger_pct": 0.25,
        "break_even_buffer_pct": 0.005,
        "trailing_ladder": [level.model_dump() for level in ladder],
        "enable_partial_take_profit": False,
        "enable_addon_alert": False,
        "enable_tech_sell_while_holding": False,
    }


def get_strategy() -> dict:
    with get_db() as session:
        row = session.scalars(
            select(StrategyConfig).where(StrategyConfig.id == 1)
        ).first()
    if row is None:
        return _default_strategy_dict()
    return _row_to_strategy_dict(row)


def _override_to_dict(row: StockStrategyOverride | None) -> dict:
    if row is None:
        return {
            "custom_n": None,
            "custom_x": None,
            "custom_y": None,
            "stop_loss_pct": None,
            "break_even_trigger_pct": None,
            "break_even_buffer_pct": None,
            "trailing_ladder": None,
            "enable_partial_take_profit": None,
            "enable_addon_alert": None,
            "enable_tech_sell_while_holding": None,
            "has_override": False,
        }

    ladder = None
    if row.trailing_ladder_json:
        ladder = [
            level.model_dump()
            for level in parse_trailing_ladder(row.trailing_ladder_json)
        ]

    def maybe_bool(value: int | None) -> bool | None:
        if value is None:
            return None
        return bool(value)

    return {
        "custom_n": row.custom_n,
        "custom_x": row.custom_x,
        "custom_y": row.custom_y,
        "stop_loss_pct": row.stop_loss_pct,
        "break_even_trigger_pct": row.break_even_trigger_pct,
        "break_even_buffer_pct": row.break_even_buffer_pct,
        "trailing_ladder": ladder,
        "enable_partial_take_profit": maybe_bool(row.enable_partial_take_profit),
        "enable_addon_alert": maybe_bool(row.enable_addon_alert),
        "enable_tech_sell_while_holding": maybe_bool(row.enable_tech_sell_while_holding),
        "has_override": True,
    }


def get_override(stock_code: str) -> dict:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        wl = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized)
            .first()
        )
        if wl is None:
            raise ValueError("stock not found in watchlist")
        row = session.get(StockStrategyOverride, normalized)
        # Merge legacy watchlist custom_n/x/y if override row missing those.
        result = _override_to_dict(row)
        if result["custom_n"] is None and wl.custom_n is not None:
            result["custom_n"] = wl.custom_n
            result["has_override"] = True
        if result["custom_x"] is None and wl.custom_x is not None:
            result["custom_x"] = wl.custom_x
            result["has_override"] = True
        if result["custom_y"] is None and wl.custom_y is not None:
            result["custom_y"] = wl.custom_y
            result["has_override"] = True
        result["stock_code"] = normalized

    result["resolved"] = _serialize_resolved(resolve_params(normalized))
    return result


def _serialize_resolved(resolved: dict) -> dict:
    out = dict(resolved)
    ladder = out.get("trailing_ladder") or []
    out["trailing_ladder"] = [
        level.model_dump() if hasattr(level, "model_dump") else level for level in ladder
    ]
    return out


def resolve_params(stock_code: str | None = None) -> dict:
    """Merge global strategy with optional per-stock overrides."""
    with get_db() as session:
        global_row = session.scalars(
            select(StrategyConfig).where(StrategyConfig.id == 1)
        ).first()
        global_dict = (
            _row_to_strategy_dict(global_row)
            if global_row is not None
            else _default_strategy_dict()
        )

        override: StockStrategyOverride | None = None
        wl: Watchlist | None = None
        if stock_code:
            normalized = normalize_stock_code(stock_code)
            override = session.get(StockStrategyOverride, normalized)
            wl = (
                session.query(Watchlist)
                .filter(Watchlist.stock_code == normalized)
                .first()
            )

    n = global_dict["global_buy_n"]
    x = global_dict["global_buy_x"]
    y = global_dict["global_sell_y"]
    # Prefer larger of buy/sell N for baseline window consistency with engine SQL.
    n = max(global_dict["global_buy_n"], global_dict["global_sell_n"])

    stop_loss_pct = global_dict["stop_loss_pct"]
    break_even_trigger_pct = global_dict["break_even_trigger_pct"]
    break_even_buffer_pct = global_dict["break_even_buffer_pct"]
    trailing_ladder = parse_trailing_ladder(global_dict["trailing_ladder"])
    enable_partial = global_dict["enable_partial_take_profit"]
    enable_addon = global_dict["enable_addon_alert"]
    enable_tech_sell = global_dict["enable_tech_sell_while_holding"]

    if wl is not None:
        if wl.custom_n is not None:
            n = wl.custom_n
        if wl.custom_x is not None:
            x = wl.custom_x
        if wl.custom_y is not None:
            y = wl.custom_y

    if override is not None:
        if override.custom_n is not None:
            n = override.custom_n
        if override.custom_x is not None:
            x = override.custom_x
        if override.custom_y is not None:
            y = override.custom_y
        if override.stop_loss_pct is not None:
            stop_loss_pct = float(override.stop_loss_pct)
        if override.break_even_trigger_pct is not None:
            break_even_trigger_pct = float(override.break_even_trigger_pct)
        if override.break_even_buffer_pct is not None:
            break_even_buffer_pct = float(override.break_even_buffer_pct)
        if override.trailing_ladder_json:
            trailing_ladder = parse_trailing_ladder(override.trailing_ladder_json)
        if override.enable_partial_take_profit is not None:
            enable_partial = bool(override.enable_partial_take_profit)
        if override.enable_addon_alert is not None:
            enable_addon = bool(override.enable_addon_alert)
        if override.enable_tech_sell_while_holding is not None:
            enable_tech_sell = bool(override.enable_tech_sell_while_holding)

    return {
        "n": int(n),
        "x": float(x),
        "y": float(y),
        "stop_loss_pct": float(stop_loss_pct),
        "break_even_trigger_pct": float(break_even_trigger_pct),
        "break_even_buffer_pct": float(break_even_buffer_pct),
        "trailing_ladder": trailing_ladder,
        "enable_partial_take_profit": bool(enable_partial),
        "enable_addon_alert": bool(enable_addon),
        "enable_tech_sell_while_holding": bool(enable_tech_sell),
    }


def _recalc_position_stop(
    session,
    pos: Position,
    *,
    price: float | None = None,
) -> None:
    if pos.qty <= 0 or pos.avg_cost is None or pos.avg_cost <= 0:
        return
    params = resolve_params(pos.stock_code)
    risk = risk_params_from_resolved(params)
    eval_price = price
    if eval_price is None or eval_price <= 0:
        eval_price = pos.highest_since_hold or pos.avg_cost
    result = evaluate(
        avg_cost=float(pos.avg_cost),
        highest_since_hold=pos.highest_since_hold,
        prev_stop=pos.stop_price,
        price=float(eval_price),
        params=risk,
        prev_ladder_idx=None,
    )
    pos.highest_since_hold = result.highest_since_hold
    pos.stop_price = result.stop_price
    pos.updated_at = _utcnow()


def recalc_holding_stops(
    *,
    stock_code: str | None = None,
    price_by_code: dict[str, float] | None = None,
) -> int:
    """Recompute stop_price for holdings after param changes. Returns updated count."""
    price_by_code = price_by_code or {}
    updated = 0
    with get_db() as session:
        query = session.query(Position).filter(Position.qty > 0)
        if stock_code:
            query = query.filter(Position.stock_code == normalize_stock_code(stock_code))
        rows = query.all()
        for pos in rows:
            # resolve_params opens its own session — fine for read consistency here.
            _recalc_position_stop(
                session,
                pos,
                price=price_by_code.get(pos.stock_code),
            )
            updated += 1
        session.commit()
    return updated


def update_strategy(payload: StrategyConfigSchema) -> dict:
    with get_db() as session:
        row = session.scalars(
            select(StrategyConfig).where(StrategyConfig.id == 1)
        ).first()
        if row is None:
            row = StrategyConfig(id=1)
            session.add(row)
        row.global_buy_n = payload.global_buy_n
        row.global_buy_x = payload.global_buy_x
        row.global_sell_n = payload.global_sell_n
        row.global_sell_y = payload.global_sell_y
        row.stop_loss_pct = payload.stop_loss_pct
        row.break_even_trigger_pct = payload.break_even_trigger_pct
        row.break_even_buffer_pct = payload.break_even_buffer_pct
        row.trailing_ladder_json = ladder_to_json(payload.trailing_ladder)
        row.enable_partial_take_profit = _bool_to_int(payload.enable_partial_take_profit)
        row.enable_addon_alert = _bool_to_int(payload.enable_addon_alert)
        row.enable_tech_sell_while_holding = _bool_to_int(
            payload.enable_tech_sell_while_holding
        )
        row.updated_at = _utcnow()
        session.commit()

    recalc_holding_stops()
    return get_strategy()


def update_override(stock_code: str, payload: StockStrategyOverrideSchema) -> dict:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        wl = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized)
            .first()
        )
        if wl is None:
            raise ValueError("stock not found in watchlist")

        row = session.get(StockStrategyOverride, normalized)
        if row is None:
            row = StockStrategyOverride(stock_code=normalized)
            session.add(row)

        row.custom_n = payload.custom_n
        row.custom_x = payload.custom_x
        row.custom_y = payload.custom_y
        row.stop_loss_pct = payload.stop_loss_pct
        row.break_even_trigger_pct = payload.break_even_trigger_pct
        row.break_even_buffer_pct = payload.break_even_buffer_pct
        row.trailing_ladder_json = (
            ladder_to_json(payload.trailing_ladder)
            if payload.trailing_ladder is not None
            else None
        )
        row.enable_partial_take_profit = (
            None
            if payload.enable_partial_take_profit is None
            else _bool_to_int(payload.enable_partial_take_profit)
        )
        row.enable_addon_alert = (
            None
            if payload.enable_addon_alert is None
            else _bool_to_int(payload.enable_addon_alert)
        )
        row.enable_tech_sell_while_holding = (
            None
            if payload.enable_tech_sell_while_holding is None
            else _bool_to_int(payload.enable_tech_sell_while_holding)
        )
        row.updated_at = _utcnow()

        # Keep legacy watchlist columns in sync for N/X/Y engine SQL.
        wl.custom_n = payload.custom_n
        wl.custom_x = payload.custom_x
        wl.custom_y = payload.custom_y
        session.commit()

    recalc_holding_stops(stock_code=normalized)
    return get_override(normalized)


def clear_override(stock_code: str) -> dict:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        wl = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized)
            .first()
        )
        if wl is None:
            raise ValueError("stock not found in watchlist")
        row = session.get(StockStrategyOverride, normalized)
        if row is not None:
            session.delete(row)
        wl.custom_n = None
        wl.custom_x = None
        wl.custom_y = None
        session.commit()

    recalc_holding_stops(stock_code=normalized)
    return get_override(normalized)
