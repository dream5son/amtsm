from __future__ import annotations

from datetime import datetime, timezone

from app.db.connection import get_db
from app.db.models import Position, PositionLedger, Watchlist
from app.engine.market_hours import to_api_iso
from app.schemas.position import PositionTradeRequest
from app.services.position_math import (
    realized_pnl,
    weighted_avg_cost,
)
from app.services.stock_search_service import normalize_stock_code
from app.services.strategy_service import resolve_params
from app.market_signal import evaluate, risk_params_from_resolved


class StockNotFoundError(ValueError):
    """Raised when stock_code is not in watchlist."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_watchlist(session, stock_code: str) -> None:
    exists = (
        session.query(Watchlist.stock_code)
        .filter(Watchlist.stock_code == stock_code)
        .first()
    )
    if not exists:
        raise StockNotFoundError("stock not found in watchlist")


def _get_or_create_position(session, stock_code: str) -> Position:
    pos = session.get(Position, stock_code)
    if pos is None:
        pos = Position(
            stock_code=stock_code,
            qty=0,
            avg_cost=None,
            highest_since_hold=None,
            stop_price=None,
            position_status="EMPTY",
            opened_at=None,
        )
        session.add(pos)
        session.flush()
    return pos


def _initial_stop_from_evaluate(
    *,
    new_avg: float,
    highest_since_hold: float | None,
    prev_stop: float | None,
    stock_code: str,
) -> float:
    """Compute stop via the shared three-tier evaluate (aligns with live/backtest)."""
    risk = risk_params_from_resolved(resolve_params(stock_code))
    result = evaluate(
        avg_cost=new_avg,
        highest_since_hold=highest_since_hold,
        prev_stop=prev_stop,
        price=new_avg,
        params=risk,
    )
    return float(result.stop_price)


def _position_dict(pos: Position) -> dict:
    return {
        "stock_code": pos.stock_code,
        "qty": pos.qty,
        "avg_cost": pos.avg_cost,
        "highest_since_hold": pos.highest_since_hold,
        "stop_price": pos.stop_price,
        "position_status": pos.position_status,
        "opened_at": to_api_iso(pos.opened_at),
    }


def preview_buy(stock_code: str, qty: int, price: float) -> dict:
    if qty <= 0:
        raise ValueError("qty must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        _require_watchlist(session, normalized)
        pos = session.get(Position, normalized)
        old_qty = pos.qty if pos else 0
        old_cost = pos.avg_cost if pos else None
        old_stop = pos.stop_price if pos else None
        old_highest = pos.highest_since_hold if pos else None

        new_avg = weighted_avg_cost(old_cost, old_qty, price, qty)
        if old_qty > 0:
            highest = old_highest
            prev_stop = old_stop
        else:
            highest = new_avg
            prev_stop = None
        new_stop = _initial_stop_from_evaluate(
            new_avg=new_avg,
            highest_since_hold=highest,
            prev_stop=prev_stop,
            stock_code=normalized,
        )

        return {
            "stock_code": normalized,
            "old_qty": old_qty,
            "new_qty": old_qty + qty,
            "old_avg_cost": old_cost,
            "new_avg_cost": new_avg,
            "old_stop_price": old_stop,
            "new_stop_price": new_stop,
            "is_addon": old_qty > 0,
        }


def register_buy(stock_code: str, payload: PositionTradeRequest) -> dict:
    normalized = normalize_stock_code(stock_code)
    trade_date = payload.trade_date.isoformat()

    with get_db() as session:
        _require_watchlist(session, normalized)
        pos = _get_or_create_position(session, normalized)

        old_qty = pos.qty
        old_cost = pos.avg_cost
        old_stop = pos.stop_price
        old_highest = pos.highest_since_hold

        new_avg = weighted_avg_cost(old_cost, old_qty, payload.price, payload.qty)
        if old_qty == 0:
            pos.highest_since_hold = payload.price
            pos.opened_at = _utcnow()
            highest_for_stop = float(payload.price)
            prev_stop = None
        else:
            # Addon: do not reset highest_since_hold
            pos.highest_since_hold = old_highest
            highest_for_stop = old_highest
            prev_stop = old_stop

        new_stop = _initial_stop_from_evaluate(
            new_avg=new_avg,
            highest_since_hold=highest_for_stop,
            prev_stop=prev_stop,
            stock_code=normalized,
        )

        pos.qty = old_qty + payload.qty
        pos.avg_cost = new_avg
        pos.stop_price = new_stop
        pos.position_status = "HOLDING"
        pos.updated_at = _utcnow()

        ledger = PositionLedger(
            stock_code=normalized,
            side="BUY",
            qty=payload.qty,
            price=payload.price,
            trade_date=trade_date,
            realized_pnl=None,
            note=payload.note,
        )
        session.add(ledger)
        session.commit()
        session.refresh(ledger)
        session.refresh(pos)

        return {"position": _position_dict(pos), "ledger_id": ledger.id}


def register_sell(stock_code: str, payload: PositionTradeRequest) -> dict:
    normalized = normalize_stock_code(stock_code)
    trade_date = payload.trade_date.isoformat()

    with get_db() as session:
        _require_watchlist(session, normalized)
        pos = session.get(Position, normalized)
        if pos is None or pos.qty <= 0:
            raise ValueError("no position to sell")
        if payload.qty > pos.qty:
            raise ValueError("sell qty exceeds position qty")
        if pos.avg_cost is None or pos.avg_cost <= 0:
            raise ValueError("invalid position cost")

        pnl = realized_pnl(payload.price, pos.avg_cost, payload.qty)
        remaining = pos.qty - payload.qty

        if remaining == 0:
            pos.qty = 0
            pos.avg_cost = None
            pos.highest_since_hold = None
            pos.stop_price = None
            pos.position_status = "EMPTY"
            pos.opened_at = None
        else:
            pos.qty = remaining
            # avg_cost unchanged on partial sell
            pos.position_status = "PARTIAL"

        pos.updated_at = _utcnow()

        ledger = PositionLedger(
            stock_code=normalized,
            side="SELL",
            qty=payload.qty,
            price=payload.price,
            trade_date=trade_date,
            realized_pnl=pnl,
            note=payload.note,
        )
        session.add(ledger)
        session.commit()
        session.refresh(ledger)
        session.refresh(pos)

        return {
            "position": _position_dict(pos),
            "ledger_id": ledger.id,
            "realized_pnl": pnl,
        }


def list_ledgers(stock_code: str) -> list[dict]:
    normalized = normalize_stock_code(stock_code)

    with get_db() as session:
        _require_watchlist(session, normalized)
        rows = (
            session.query(PositionLedger)
            .filter(PositionLedger.stock_code == normalized)
            .order_by(PositionLedger.trade_date.desc(), PositionLedger.id.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "stock_code": row.stock_code,
                "side": row.side,
                "qty": row.qty,
                "price": row.price,
                "trade_date": row.trade_date,
                "realized_pnl": row.realized_pnl,
                "note": row.note,
                "created_at": to_api_iso(row.created_at),
            }
            for row in rows
        ]
