from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    stock_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="NORMAL")
    custom_n: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    custom_x: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    custom_y: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class StrategyConfig(Base):
    __tablename__ = "strategy_config"
    __table_args__ = (CheckConstraint("id = 1", name="ck_strategy_config_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_buy_n: Mapped[int] = mapped_column(Integer, default=60)
    global_buy_x: Mapped[float] = mapped_column(Float, default=1.10)
    global_sell_n: Mapped[int] = mapped_column(Integer, default=60)
    global_sell_y: Mapped[float] = mapped_column(Float, default=0.90)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=0.08)
    break_even_trigger_pct: Mapped[float] = mapped_column(Float, default=0.10)
    break_even_buffer_pct: Mapped[float] = mapped_column(Float, default=0.005)
    trailing_ladder_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enable_partial_take_profit: Mapped[int] = mapped_column(Integer, default=0)
    enable_addon_alert: Mapped[int] = mapped_column(Integer, default=0)
    enable_tech_sell_while_holding: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class StockStrategyOverride(Base):
    __tablename__ = "stock_strategy_overrides"

    stock_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("watchlist.stock_code"), primary_key=True
    )
    custom_n: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    custom_x: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    custom_y: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    stop_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    break_even_trigger_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    break_even_buffer_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    trailing_ladder_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enable_partial_take_profit: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    enable_addon_alert: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    enable_tech_sell_while_holding: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class Position(Base):
    __tablename__ = "positions"

    stock_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("watchlist.stock_code"), primary_key=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_since_hold: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EMPTY"
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class PositionLedger(Base):
    __tablename__ = "position_ledgers"
    __table_args__ = (
        Index("idx_ledger_stock_time", "stock_code", "trade_date", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY | SELL
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class DailyBaseline(Base):
    __tablename__ = "daily_baselines"

    stock_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)  # DATE as ISO
    low_min: Mapped[float] = mapped_column(Float, nullable=False)
    high_max: Mapped[float] = mapped_column(Float, nullable=False)
    actual_n: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class DailyMarketSnapshot(Base):
    __tablename__ = "daily_market_snapshots"

    stock_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class AlertLog(Base):
    __tablename__ = "alert_logs"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "trade_date", "signal_type", name="idx_freq_limit"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_price: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_price: Mapped[float] = mapped_column(Float, nullable=False)
    used_coeff: Mapped[float] = mapped_column(Float, nullable=False)
    sent_status: Mapped[str] = mapped_column(String(10), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_time: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class BaselineJobLog(Base):
    __tablename__ = "baseline_job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(50), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="RUNNING")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class BaselineJobLogItem(Base):
    __tablename__ = "baseline_job_log_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_log_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("baseline_job_logs.id"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    strategy_n: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    low_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class BacktestJob(Base):
    __tablename__ = "backtest_jobs"
    __table_args__ = (
        Index(
            "idx_bt_jobs_stock_status",
            "stock_code",
            "status",
            "created_at",
        ),
        Index("idx_bt_jobs_compare_group", "compare_group_id"),
        Index(
            "uq_bt_jobs_inflight",
            "stock_code",
            "params_hash",
            "start_date",
            "end_date",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compare_group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_win_loss_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_insufficient: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    __table_args__ = (Index("idx_bt_trades_job", "job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_jobs.id"), nullable=False
    )
    entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_date: Mapped[str] = mapped_column(String(10), nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    hold_days: Mapped[int] = mapped_column(Integer, nullable=False)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)
