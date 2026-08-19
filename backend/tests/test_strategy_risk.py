"""Strategy API + resolve_params + stop recalc (US05/07)."""

from __future__ import annotations

from sqlalchemy import text

from app.config import settings
from app.db.connection import get_db, get_engine
from app.db.init_db import _migrate_factory_risk_defaults, init_db
from app.db.models import DailyMarketSnapshot, Position, Watchlist
from app.db.models import StrategyConfig as StrategyConfigRow
from app.schemas.strategy import (
    LEGACY_TRAILING_LADDER,
    StockStrategyOverride,
    StrategyConfig,
    TrailingLadderLevel,
    ladder_to_json,
    parse_trailing_ladder,
)
from app.services.strategy_service import (
    get_strategy,
    resolve_params,
    update_override,
    update_strategy,
)


def _setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()


def test_get_strategy_includes_risk_defaults(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    cfg = get_strategy()
    assert cfg["stop_loss_pct"] == 0.15
    assert cfg["break_even_trigger_pct"] == 0.25
    assert cfg["enable_partial_take_profit"] is False
    assert len(cfg["trailing_ladder"]) == 3
    assert abs(cfg["trailing_ladder"][0]["min_pnl"] - 0.20) < 1e-9
    assert abs(cfg["trailing_ladder"][0]["drawdown"] - 0.20) < 1e-9


def test_update_strategy_recalcs_holding_stop(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    with get_db() as session:
        session.add(Watchlist(stock_code="sh600519", stock_name="茅台", status="NORMAL"))
        session.add(
            Position(
                stock_code="sh600519",
                qty=100,
                avg_cost=100.0,
                highest_since_hold=100.0,
                stop_price=92.0,
                position_status="HOLDING",
            )
        )
        session.commit()

    payload = StrategyConfig(
        global_buy_n=60,
        global_buy_x=1.1,
        global_sell_n=60,
        global_sell_y=0.9,
        stop_loss_pct=0.05,
        break_even_trigger_pct=0.10,
        break_even_buffer_pct=0.005,
        trailing_ladder=[
            TrailingLadderLevel(min_pnl=0.10, max_pnl=0.20, drawdown=0.15),
            TrailingLadderLevel(min_pnl=0.20, max_pnl=0.50, drawdown=0.10),
            TrailingLadderLevel(min_pnl=0.50, max_pnl=None, drawdown=0.06),
        ],
        enable_partial_take_profit=False,
        enable_addon_alert=False,
        enable_tech_sell_while_holding=False,
    )
    update_strategy(payload)
    with get_db() as session:
        pos = session.get(Position, "sh600519")
        assert pos is not None
        assert abs(float(pos.stop_price) - 95.0) < 1e-9


def test_stock_override_resolve_params(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    with get_db() as session:
        session.add(Watchlist(stock_code="sz000001", stock_name="平安", status="NORMAL"))
        session.commit()

    update_override(
        "sz000001",
        StockStrategyOverride(
            stop_loss_pct=0.12,
            enable_addon_alert=True,
        ),
    )
    resolved = resolve_params("sz000001")
    assert abs(resolved["stop_loss_pct"] - 0.12) < 1e-9
    assert resolved["enable_addon_alert"] is True
    assert resolved["enable_partial_take_profit"] is False

    global_resolved = resolve_params(None)
    assert abs(global_resolved["stop_loss_pct"] - 0.15) < 1e-9


def test_factory_risk_defaults_migrated_from_legacy(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_db() as session:
        row = session.get(StrategyConfigRow, 1)
        assert row is not None
        row.stop_loss_pct = 0.08
        row.break_even_trigger_pct = 0.10
        row.trailing_ladder_json = ladder_to_json(parse_trailing_ladder(LEGACY_TRAILING_LADDER))
        session.commit()

    _migrate_factory_risk_defaults()
    cfg = get_strategy()
    assert abs(cfg["stop_loss_pct"] - 0.15) < 1e-9
    assert abs(cfg["break_even_trigger_pct"] - 0.25) < 1e-9
    assert abs(cfg["trailing_ladder"][0]["drawdown"] - 0.20) < 1e-9


def test_custom_risk_not_overwritten_by_factory_migration(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_db() as session:
        row = session.get(StrategyConfigRow, 1)
        assert row is not None
        row.stop_loss_pct = 0.05
        row.break_even_trigger_pct = 0.10
        session.commit()

    _migrate_factory_risk_defaults()
    cfg = get_strategy()
    assert abs(cfg["stop_loss_pct"] - 0.05) < 1e-9


def test_qfq_snapshot_cache_cleared_once(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600660",
                trade_date="2017-08-29",
                open_price=12.0,
                high_price=12.2,
                low_price=11.8,
                close_price=12.1,
                volume=1000.0,
            )
        )
        session.commit()

    init_db()
    with get_db() as session:
        assert session.query(DailyMarketSnapshot).count() == 1

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM schema_meta"))

    init_db()
    with get_db() as session:
        assert session.query(DailyMarketSnapshot).count() == 0


def test_unadjusted_cache_migrates_back_to_qfq(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600900",
                trade_date="2010-07-20",
                open_price=8.11,
                high_price=8.14,
                low_price=8.02,
                close_price=8.13,
                volume=1000.0,
            )
        )
        session.commit()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_meta(key, value) VALUES ('price_adjust', 'none') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        )

    init_db()
    with get_db() as session:
        assert session.query(DailyMarketSnapshot).count() == 0
    with get_engine().begin() as conn:
        value = conn.execute(
            text("SELECT value FROM schema_meta WHERE key = 'price_adjust'")
        ).scalar()
    assert value == "qfq_v2"

