"""Tests for per-stock alert history listing (paginated)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.init_db import init_db
from app.engine.state import runtime_state
from app.main import app
from app.schemas.watchlist import WatchlistCreate
from app.services.alert_service import insert_alert, list_alerts_by_stock
from app.services.watchlist_service import add_watchlist


def _setup(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))


def _insert(
    *,
    trade_date: str,
    signal_type: str = "BUY",
    stock_code: str = "sh600519",
    sent_status: str = "SUCCESS",
    error_message: str | None = None,
) -> int:
    row_id = insert_alert(
        stock_code=stock_code,
        trade_date=trade_date,
        signal_type=signal_type,
        trigger_price=100.0,
        baseline_price=90.0,
        used_coeff=1.1,
        sent_status=sent_status,
        error_message=error_message,
    )
    assert row_id is not None
    return row_id


def test_list_alerts_empty(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    page = list_alerts_by_stock("600519")
    assert page["items"] == []
    assert page["total"] == 0
    assert page["limit"] == 20
    assert page["offset"] == 0


def test_list_alerts_paginates_newest_first(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    for day in range(1, 26):
        _insert(trade_date=f"2026-01-{day:02d}")
    _insert(
        stock_code="__SYS__",
        trade_date="2026-01-25",
        signal_type="DELAY",
        sent_status="INTERNAL",
    )

    first = list_alerts_by_stock("sh600519", limit=20, offset=0)
    assert first["total"] == 25
    assert first["limit"] == 20
    assert first["offset"] == 0
    assert len(first["items"]) == 20
    assert first["items"][0]["trade_date"] == "2026-01-25"
    assert first["items"][-1]["trade_date"] == "2026-01-06"
    assert first["items"][0]["sent_time"] is None or isinstance(
        first["items"][0]["sent_time"], str
    )

    second = list_alerts_by_stock("600519", limit=20, offset=20)
    assert second["total"] == 25
    assert len(second["items"]) == 5
    assert second["items"][0]["trade_date"] == "2026-01-05"
    assert second["items"][-1]["trade_date"] == "2026-01-01"


def test_list_alerts_offset_past_total(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    _insert(trade_date="2026-01-01")

    page = list_alerts_by_stock("600519", limit=20, offset=50)
    assert page["items"] == []
    assert page["total"] == 1
    assert page["offset"] == 50


def test_list_alerts_rejects_invalid_paging(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="limit out of range"):
        list_alerts_by_stock("600519", limit=0)
    with pytest.raises(ValueError, match="limit out of range"):
        list_alerts_by_stock("600519", limit=201)
    with pytest.raises(ValueError, match="offset must be >= 0"):
        list_alerts_by_stock("600519", offset=-1)


def test_list_alerts_requires_watchlist(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()

    with pytest.raises(ValueError, match="stock not found in watchlist"):
        list_alerts_by_stock("600519")


def test_get_alerts_api_empty_and_not_found(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    client = TestClient(app)

    empty = client.get("/api/alerts/600519")
    assert empty.status_code == 200
    body = empty.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == 20
    assert body["offset"] == 0

    missing = client.get("/api/alerts/000001")
    assert missing.status_code == 404

    bad_code = client.get("/api/alerts/abc")
    assert bad_code.status_code == 400


def test_get_alerts_api_pagination_and_validation(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    for day in range(1, 6):
        _insert(trade_date=f"2026-02-{day:02d}", signal_type="SELL")
    _insert(
        trade_date="2026-02-05",
        signal_type="STOP_LOSS",
        sent_status="FAILED",
        error_message="send failed",
    )
    client = TestClient(app)

    first = client.get("/api/alerts/sh600519", params={"limit": 2, "offset": 0})
    assert first.status_code == 200
    data = first.json()
    assert data["total"] == 6
    assert len(data["items"]) == 2
    assert data["items"][0]["trade_date"] == "2026-02-05"
    assert data["items"][0]["signal_type"] == "STOP_LOSS"
    assert data["items"][1]["signal_type"] == "SELL"

    second = client.get("/api/alerts/600519", params={"limit": 2, "offset": 2})
    assert second.status_code == 200
    assert len(second.json()["items"]) == 2
    assert second.json()["total"] == 6

    past_end = client.get("/api/alerts/600519", params={"limit": 20, "offset": 100})
    assert past_end.status_code == 200
    assert past_end.json()["items"] == []
    assert past_end.json()["total"] == 6

    bad_limit = client.get("/api/alerts/600519", params={"limit": 0})
    assert bad_limit.status_code == 400

    bad_offset = client.get("/api/alerts/600519", params={"offset": -1})
    assert bad_offset.status_code == 400
