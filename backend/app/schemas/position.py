from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class PositionTradeRequest(BaseModel):
    qty: int = Field(gt=0)
    price: float = Field(gt=0)
    trade_date: date
    note: str | None = None


class BuyPreviewResponse(BaseModel):
    stock_code: str
    old_qty: int
    new_qty: int
    old_avg_cost: float | None
    new_avg_cost: float
    old_stop_price: float | None
    new_stop_price: float
    is_addon: bool


class PositionSnapshotResponse(BaseModel):
    stock_code: str
    qty: int
    avg_cost: float | None
    highest_since_hold: float | None
    stop_price: float | None
    position_status: Literal["EMPTY", "HOLDING", "PARTIAL"]
    opened_at: str | None = None


class BuyResponse(BaseModel):
    position: PositionSnapshotResponse
    ledger_id: int


class SellResponse(BaseModel):
    position: PositionSnapshotResponse
    ledger_id: int
    realized_pnl: float


class LedgerItem(BaseModel):
    id: int
    stock_code: str
    side: Literal["BUY", "SELL"]
    qty: int
    price: float
    trade_date: str
    realized_pnl: float | None
    note: str | None
    created_at: str | None
