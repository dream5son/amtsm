from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import DailyBaseline, DailyMarketSnapshot, Watchlist
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
            "latest_price": None,
            "change_pct": None,
            "actual_n": None,
            "effective_n": 60,
            "insufficient_days": None,
            "signal_type": None,
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

    assert remove_watchlist("sh600519") == 1
    assert list_watchlist() == []


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


def test_list_watchlist_includes_runtime_signal_type(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))

    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    runtime_state.signal_state["sh600519"] = "BUY"
    runtime_state.signal_trade_date = "2026-08-01"

    rows = list_watchlist()
    assert rows[0]["signal_type"] == "BUY"
