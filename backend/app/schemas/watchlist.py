from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    stock_code: str = Field(min_length=6, max_length=10)
    stock_name: str = Field(min_length=1, max_length=50)


class WatchlistItem(BaseModel):
    stock_code: str
    stock_name: str
    status: str
    created_at: datetime
    latest_price: Optional[float] = None
    change_pct: Optional[float] = None
    actual_n: Optional[int] = None
    effective_n: int
    insufficient_days: Optional[int] = None
