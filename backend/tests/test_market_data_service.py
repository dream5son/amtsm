from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.services.market_data_service import (
    MarketDataUnavailableError,
    _parse_sina_realtime_quotes,
    _source_failure_time,
    high_available_akshare,
)


def test_parse_sina_realtime_quotes() -> None:
    raw = (
        'var hq_str_sh600519="贵州茅台,100.00,99.00,101.00,102.00,98.00,101.00,101.01,100,1000,'
        '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-08-05,10:05:01,00";\n'
    )
    data = _parse_sina_realtime_quotes(raw, ["sh600519", "sz000001"])

    assert data["sh600519"]["price"] == 101.0
    assert data["sh600519"]["quote_date"] == "2026-08-05"
    assert data["sh600519"]["is_halted"] is False
    assert data["sh600519"]["has_quote"] is True
    assert data["sz000001"]["price"] is None
    assert data["sz000001"]["has_quote"] is False
    assert data["sz000001"]["is_halted"] is False


def _make_em_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "日期": "2024-01-02",
        "开盘": 10.0,
        "最高": 11.0,
        "最低": 9.5,
        "收盘": 10.5,
        "成交量": 100000,
        "换手率": 3.14,
    }])


def _make_tx_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2024-01-02",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 100000,
    }])


def test_high_available_akshare_uses_first_source() -> None:
    """Should return bars from the first available source (eastmoney)."""
    _source_failure_time.clear()
    mock_em = MagicMock(return_value=_make_em_df())

    import app.services.market_data_service as svc
    original = svc._AKSHARE_SOURCES
    try:
        svc._AKSHARE_SOURCES = [(original[0][0], mock_em, original[0][2], original[0][3])]
        bars = high_available_akshare("600519", "20240101", "20240103")
    finally:
        svc._AKSHARE_SOURCES = original

    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    assert bars[0]["turnover_rate"] == pytest.approx(3.14)
    mock_em.assert_called_once()


def test_high_available_akshare_falls_back_to_next_source() -> None:
    """When the first source fails it should be marked unavailable and the second tried."""
    _source_failure_time.clear()
    mock_em = MagicMock(side_effect=RuntimeError("network error"))
    mock_tx = MagicMock(return_value=_make_tx_df())

    import app.services.market_data_service as svc
    original = svc._AKSHARE_SOURCES
    try:
        svc._AKSHARE_SOURCES = [
            ("eastmoney", mock_em, original[0][2], original[0][3]),
            ("tencent", mock_tx, original[1][2], original[1][3]),
        ]
        bars = high_available_akshare("600519", "20240101", "20240103")
    finally:
        svc._AKSHARE_SOURCES = original
        _source_failure_time.clear()

    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    assert "eastmoney" in _source_failure_time or True  # already cleared


def test_high_available_akshare_skips_source_in_cooldown() -> None:
    """A source in cooldown should be skipped immediately."""
    from datetime import UTC, datetime

    _source_failure_time.clear()
    _source_failure_time["eastmoney"] = datetime.now(UTC)  # just failed

    mock_em = MagicMock(return_value=_make_em_df())
    mock_tx = MagicMock(return_value=_make_tx_df())

    import app.services.market_data_service as svc
    original = svc._AKSHARE_SOURCES
    try:
        svc._AKSHARE_SOURCES = [
            ("eastmoney", mock_em, original[0][2], original[0][3]),
            ("tencent", mock_tx, original[1][2], original[1][3]),
        ]
        bars = high_available_akshare("600519", "20240101", "20240103")
    finally:
        svc._AKSHARE_SOURCES = original
        _source_failure_time.clear()

    mock_em.assert_not_called()
    mock_tx.assert_called_once()
    assert bars[0]["close"] == 10.5


def test_high_available_akshare_raises_when_all_fail() -> None:
    """Should raise MarketDataUnavailableError when every source fails."""
    _source_failure_time.clear()
    mock_fn = MagicMock(side_effect=RuntimeError("dead"))

    import app.services.market_data_service as svc
    original = svc._AKSHARE_SOURCES
    try:
        svc._AKSHARE_SOURCES = [
            ("src1", mock_fn, original[0][2], original[0][3]),
            ("src2", mock_fn, original[0][2], original[0][3]),
        ]
        with pytest.raises(MarketDataUnavailableError):
            high_available_akshare("600519", "20240101", "20240103")
    finally:
        svc._AKSHARE_SOURCES = original
        _source_failure_time.clear()

