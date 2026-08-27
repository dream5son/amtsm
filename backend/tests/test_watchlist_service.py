from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import (
    DailyBaseline,
    DailyMarketSnapshot,
    StockStrategyOverride,
    Watchlist,
)
from app.engine.state import runtime_state
from app.schemas.watchlist import WatchlistCreate
from app.services.watchlist_service import (
    add_watchlist,
    list_watchlist,
    remove_watchlist,
)


def test_add_watchlist_normalizes_code_and_rejects_duplicates(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()

    payload = WatchlistCreate(stock_code="600519", stock_name="贵州茅台")
    add_watchlist(payload)

    rows = list_watchlist()
    assert rows == [
        {
            "stock_code": "sh600519",
            "stock_name": "贵州茅台",
            "status": "NORMAL",
            "created_at": rows[0]["created_at"],
            "signal_strategy_id": None,
            "effective_signal_strategy_id": rows[0]["effective_signal_strategy_id"],
            "effective_signal_strategy_name": "峰谷量价策略",
            "latest_price": None,
            "change_pct": None,
            "actual_n": None,
            "effective_n": 60,
            "insufficient_days": None,
            "signal_type": None,
            "signal_t1_note": False,
            "signal_limit_board": False,
            "position_status": "EMPTY",
            "position_qty": 0,
            "avg_cost": None,
            "stop_price": None,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "stop_distance_pct": None,
            "backtest_status": "NONE",
            "backtest_job_id": None,
            "backtest_win_rate": None,
            "backtest_trade_count": None,
            "backtest_sample_insufficient": False,
            "backtest_stale": False,
            "backtest_error_message": None,
        }
    ]

    duplicate = WatchlistCreate(stock_code="sh600519", stock_name="贵州茅台")
    try:
        add_watchlist(duplicate)
    except ValueError as exc:
        assert str(exc) == "stock already exists"
    else:
        raise AssertionError("expected duplicate add to fail")


def test_remove_watchlist_normalizes_code(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    runtime_state.baseline_cache["sh600519"] = {"trade_date": "2026-08-05"}
    runtime_state.set_signal("sh600519", "BUY")
    runtime_state.position_cache["sh600519"] = {"qty": 100}
    runtime_state.partial_tp_ladder_idx["sh600519"] = 1
    runtime_state.exit_fired_today.add("sh600519")
    runtime_state.sent_signal_keys.add(("sh600519", "2026-08-05", "BUY"))
    runtime_state.daily_cap_reached.add(("sh600519", "2026-08-05"))
    runtime_state.baseline_cache["sz000001"] = {"trade_date": "2026-08-05"}
    runtime_state.set_signal("sz000001", "SELL")

    assert remove_watchlist("sh600519") == 1
    assert list_watchlist() == []
    assert "sh600519" not in runtime_state.baseline_cache
    assert "sh600519" not in runtime_state.signal_state
    assert "sh600519" not in runtime_state.signal_meta
    assert "sh600519" not in runtime_state.position_cache
    assert "sh600519" not in runtime_state.partial_tp_ladder_idx
    assert "sh600519" not in runtime_state.exit_fired_today
    assert ("sh600519", "2026-08-05", "BUY") not in runtime_state.sent_signal_keys
    assert ("sh600519", "2026-08-05") not in runtime_state.daily_cap_reached
    assert runtime_state.baseline_cache["sz000001"]["trade_date"] == "2026-08-05"
    assert runtime_state.signal_state["sz000001"] == "SELL"


def test_remove_watchlist_deletes_strategy_override(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            StockStrategyOverride(stock_code="sh600519", custom_n=30, custom_x=1.05)
        )
        session.commit()

    assert remove_watchlist("sh600519") == 1
    with get_db() as session:
        assert session.get(StockStrategyOverride, "sh600519") is None
        assert session.query(Watchlist).filter_by(stock_code="sh600519").one_or_none() is None


def test_list_watchlist_includes_market_fields_and_insufficient_days(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        row = session.query(Watchlist).filter_by(stock_code="sh600519").one()
        row.custom_n = 10
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-08-01",
                open_price=100.0,
                high_price=101.0,
                low_price=99.0,
                close_price=104.0,
                volume=1000000.0,
            )
        )
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-01",
                low_min=90.0,
                high_max=120.0,
                actual_n=6,
            )
        )
        session.commit()

    rows = list_watchlist()
    assert len(rows) == 1
    item = rows[0]
    assert item["latest_price"] == 104.0
    assert item["change_pct"] == 4.0
    assert item["actual_n"] == 6
    assert item["effective_n"] == 10
    assert item["insufficient_days"] == 4
    assert item["signal_type"] is None
    assert item["position_status"] == "EMPTY"
    assert item["position_qty"] == 0
    assert item["avg_cost"] is None
    assert item["stop_price"] is None
    assert item["unrealized_pnl"] is None
    assert item["unrealized_pnl_pct"] is None
    assert item["stop_distance_pct"] is None


def test_list_watchlist_includes_position_pnl(tmp_path, monkeypatch) -> None:
    from datetime import date

    from app.schemas.position import PositionTradeRequest
    from app.services.position_service import register_buy

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    register_buy(
        "600519",
        PositionTradeRequest(qty=100, price=100.0, trade_date=date(2026, 8, 1)),
    )

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-08-02",
                open_price=100.0,
                high_price=110.0,
                low_price=99.0,
                close_price=110.0,
                volume=1000000.0,
            )
        )
        session.commit()

    item = list_watchlist()[0]
    assert item["position_status"] == "HOLDING"
    assert item["position_qty"] == 100
    assert item["avg_cost"] == 100.0
    assert abs(item["unrealized_pnl"] - 1000.0) < 1e-9
    assert abs(item["unrealized_pnl_pct"] - 0.1) < 1e-9
    assert item["stop_price"] is not None
    assert item["stop_distance_pct"] is not None


def test_list_watchlist_includes_runtime_signal_type(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    runtime_state.set_signal("sh600519", "STOP_LOSS", t1_note=True)
    runtime_state.signal_trade_date = "2026-08-01"

    rows = list_watchlist()
    assert rows[0]["signal_type"] == "STOP_LOSS"
    assert rows[0]["signal_t1_note"] is True
    assert rows[0]["signal_limit_board"] is False

    runtime_state.set_signal("sh600519", "SELL", is_limit_up=True)
    rows = list_watchlist()
    assert rows[0]["signal_type"] == "SELL"
    assert rows[0]["signal_t1_note"] is False
    assert rows[0]["signal_limit_board"] is True
