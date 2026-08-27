from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.strategy import TrailingLadderLevel, validate_trailing_ladder


class BacktestParamsOverride(BaseModel):
    """One parameter group for a backtest request.

    Unset fields fall back to the stock's current effective (global + override)
    strategy params — see ``app.services.strategy_service.resolve_params``.
    """

    signal_strategy_id: int | None = Field(default=None, gt=0)
    n: int | None = Field(default=None, gt=0)
    x: float | None = Field(default=None, gt=0)
    y: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, ge=0, lt=1)
    break_even_trigger_pct: float | None = Field(default=None, ge=0, lt=1)
    break_even_buffer_pct: float | None = Field(default=None, ge=0, lt=1)
    trailing_ladder: list[TrailingLadderLevel] | None = None

    @field_validator("trailing_ladder")
    @classmethod
    def _validate_ladder(
        cls, value: list[TrailingLadderLevel] | None
    ) -> list[TrailingLadderLevel] | None:
        if value is None:
            return None
        return validate_trailing_ladder(value)


class BacktestCreateRequest(BaseModel):
    stock_code: str
    start_date: date | None = None
    end_date: date | None = None
    # Empty/omitted -> single group using the stock's current effective params.
    params: list[BacktestParamsOverride] = Field(default_factory=list)


class BacktestJobResponse(BaseModel):
    id: int
    stock_code: str
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"]
    start_date: str
    end_date: str
    params_json: str
    params_hash: str
    compare_group_id: str
    signal_strategy_id: int | None
    strategy_name_snapshot: str | None
    strategy_version: int | None
    strategy_recipe_hash: str | None
    win_rate: float | None
    avg_win_loss_ratio: float | None
    max_drawdown: float | None
    trade_count: int | None
    total_return: float | None
    annual_return: float | None
    sample_insufficient: bool
    error_message: str | None
    created_at: str | None
    finished_at: str | None


class BacktestCreateResponse(BaseModel):
    compare_group_id: str
    jobs: list[BacktestJobResponse]


class BacktestTradeResponse(BaseModel):
    id: int
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    hold_days: int
    pnl_pct: float
    pnl_amount: float | None
    exit_reason: Literal["STOP_LOSS", "TAKE_PROFIT", "PERIOD_END"]


class BacktestKlineBar(BaseModel):
    trade_date: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


class BacktestKlineResponse(BaseModel):
    job: BacktestJobResponse
    bars: list[BacktestKlineBar]
    trades: list[BacktestTradeResponse]
    has_more_before: bool = False
    has_more_after: bool = False


class WatchlistBacktestSummary(BaseModel):
    """Aggregated backtest status/summary joined onto each watchlist row."""

    backtest_status: Literal["NONE", "PENDING", "RUNNING", "SUCCESS", "FAILED"]
    backtest_job_id: int | None
    backtest_win_rate: float | None
    backtest_trade_count: int | None
    backtest_sample_insufficient: bool
    backtest_stale: bool
    backtest_error_message: str | None


class BacktestApplyRequest(BaseModel):
    """Apply a SUCCESS job's params snapshot to the stock override (story 11)."""

    update_global: bool = False


class BacktestApplyResponse(BaseModel):
    stock_code: str
    override: dict
    updated_global: bool
    global_strategy: dict | None = None
