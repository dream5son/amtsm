from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    stock_code: str = Field(min_length=6, max_length=10)
    stock_name: str = Field(min_length=1, max_length=50)


class WatchlistItem(BaseModel):
    stock_code: str
    stock_name: str
    status: str
    created_at: datetime
    signal_strategy_id: int | None = None
    effective_signal_strategy_id: int
    effective_signal_strategy_name: str
    latest_price: float | None = None
    change_pct: float | None = None
    actual_n: int | None = None
    effective_n: int
    insufficient_days: int | None = None
    signal_type: Literal["BUY", "SELL", "STOP_LOSS", "TAKE_PROFIT", "PARTIAL_TP", "ADDON"] | None = None
    signal_t1_note: bool = False
    signal_limit_board: bool = False
    position_status: Literal["EMPTY", "HOLDING", "PARTIAL"] = "EMPTY"
    position_qty: int = 0
    avg_cost: float | None = None
    stop_price: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    stop_distance_pct: float | None = None
