from __future__ import annotations

from datetime import datetime, timezone

from app.db.connection import get_db
from app.db.models import Position, PositionLedger, Watchlist
from app.schemas.position import PositionTradeRequest
from app.services.position_math import (
    initial_stop_price,
    ratchet_stop,
    realized_pnl,
    weighted_avg_cost,
)
from app.services.stock_search_service import normalize_stock_code
from app.services.strategy_service import resolve_params


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


def _stop_loss_pct(stock_code: str) -> float:
    return float(resolve_params(stock_code)["stop_loss_pct"])


def _position_dict(pos: Position) -> dict:
    return {
        "stock_code": pos.stock_code,
        "qty": pos.qty,
        "avg_cost": pos.avg_cost,
        "highest_since_hold": pos.highest_since_hold,
        "stop_price": pos.stop_price,
        "position_status": pos.position_status,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
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
        stop_pct = _stop_loss_pct(normalized)

        new_avg = weighted_avg_cost(old_cost, old_qty, price, qty)
        new_stop0 = initial_stop_price(new_avg, stop_pct)
        new_stop = ratchet_stop(old_stop if old_qty > 0 else None, new_stop0)

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
        stop_pct = _stop_loss_pct(normalized)

        old_qty = pos.qty
        old_cost = pos.avg_cost
        old_stop = pos.stop_price
        old_highest = pos.highest_since_hold

        new_avg = weighted_avg_cost(old_cost, old_qty, payload.price, payload.qty)
        new_stop0 = initial_stop_price(new_avg, stop_pct)
        new_stop = ratchet_stop(old_stop if old_qty > 0 else None, new_stop0)

        if old_qty == 0:
            pos.highest_since_hold = payload.price
            pos.opened_at = _utcnow()
        else:
            # Addon: do not reset highest_since_hold
            pos.highest_since_hold = old_highest

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
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
