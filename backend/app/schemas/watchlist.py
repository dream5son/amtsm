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
    latest_price: float | None = None
    change_pct: float | None = None
    actual_n: int | None = None
    effective_n: int
    insufficient_days: int | None = None
    signal_type: Literal["BUY", "SELL"] | None = None
