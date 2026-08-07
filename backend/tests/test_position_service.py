from datetime import date

import pytest

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import Position
from app.engine.state import runtime_state
from app.schemas.position import PositionTradeRequest
from app.schemas.watchlist import WatchlistCreate
from app.services.position_service import (
    StockNotFoundError,
    list_ledgers,
    preview_buy,
    register_buy,
    register_sell,
)
from app.services.watchlist_service import add_watchlist, list_watchlist


def _setup(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))


def test_register_buy_open_and_addon_keeps_highest(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    first = register_buy(
        "600519",
        PositionTradeRequest(qty=100, price=100.0, trade_date=date(2026, 8, 1)),
    )
    assert first["position"]["qty"] == 100
    assert first["position"]["avg_cost"] == 100.0
    assert first["position"]["position_status"] == "HOLDING"
    assert abs(first["position"]["stop_price"] - 92.0) < 1e-9
    assert first["position"]["highest_since_hold"] == 100.0

    with get_db() as session:
        pos = session.get(Position, "sh600519")
        assert pos is not None
        pos.highest_since_hold = 120.0
        session.commit()

    preview = preview_buy("600519", qty=100, price=80.0)
    assert preview["is_addon"] is True
    assert abs(preview["new_avg_cost"] - 90.0) < 1e-9

    second = register_buy(
        "600519",
        PositionTradeRequest(qty=100, price=80.0, trade_date=date(2026, 8, 2)),
    )
    assert second["position"]["qty"] == 200
    assert abs(second["position"]["avg_cost"] - 90.0) < 1e-9
    assert second["position"]["highest_since_hold"] == 120.0

    rows = list_watchlist()
    item = rows[0]
    assert item["position_status"] == "HOLDING"
    assert item["position_qty"] == 200
    assert abs(item["avg_cost"] - 90.0) < 1e-9


def test_partial_and_full_sell(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    register_buy(
        "600519",
        PositionTradeRequest(qty=1000, price=10.0, trade_date=date(2026, 8, 1)),
    )

    partial = register_sell(
        "600519",
        PositionTradeRequest(qty=400, price=12.0, trade_date=date(2026, 8, 3)),
    )
    assert partial["position"]["qty"] == 600
    assert partial["position"]["avg_cost"] == 10.0
    assert partial["position"]["position_status"] == "PARTIAL"
    assert abs(partial["realized_pnl"] - 800.0) < 1e-9

    full = register_sell(
        "600519",
        PositionTradeRequest(qty=600, price=11.0, trade_date=date(2026, 8, 4)),
    )
    assert full["position"]["qty"] == 0
    assert full["position"]["avg_cost"] is None
    assert full["position"]["stop_price"] is None
    assert full["position"]["position_status"] == "EMPTY"

    ledgers = list_ledgers("600519")
    assert len(ledgers) == 3
    assert ledgers[0]["trade_date"] == "2026-08-04"
    assert ledgers[0]["side"] == "SELL"


def test_sell_qty_exceeds_rejected(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    register_buy(
        "600519",
        PositionTradeRequest(qty=100, price=10.0, trade_date=date(2026, 8, 1)),
    )
    with pytest.raises(ValueError, match="exceeds"):
        register_sell(
            "600519",
            PositionTradeRequest(qty=200, price=10.0, trade_date=date(2026, 8, 2)),
        )


def test_unknown_stock_raises(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    with pytest.raises(StockNotFoundError):
        list_ledgers("600519")
