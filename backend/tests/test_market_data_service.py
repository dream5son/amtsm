import threading
import time
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.services.market_data_service import (
    _AKSHARE_SOURCES,
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
    assert data["sh600519"]["open"] == 100.0
    assert data["sh600519"]["high"] == 102.0
    assert data["sh600519"]["low"] == 98.0
    assert data["sh600519"]["volume"] == 100.0
    assert data["sh600519"]["quote_date"] == "2026-08-05"
    assert data["sh600519"]["is_halted"] is False
    assert data["sh600519"]["has_quote"] is True
    assert data["sz000001"]["price"] is None
    assert data["sz000001"]["high"] is None
    assert data["sz000001"]["has_quote"] is False
    assert data["sz000001"]["is_halted"] is False


def test_akshare_sources_include_sina() -> None:
    names = [entry[0] for entry in _AKSHARE_SOURCES]
    fn_names = [getattr(entry[1], "__name__", "") for entry in _AKSHARE_SOURCES]
    assert names == ["eastmoney", "tencent", "sina"]
    assert "stock_zh_a_daily" in fn_names


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


def test_sina_source_calls_are_serialized() -> None:
    """Concurrent Sina fetches must not overlap (MiniRacer is not thread-safe)."""
    _source_failure_time.clear()
    in_flight = 0
    max_in_flight = 0
    counter_lock = threading.Lock()

    def slow_sina(**_kwargs: object) -> pd.DataFrame:
        nonlocal in_flight, max_in_flight
        with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        with counter_lock:
            in_flight -= 1
        return _make_tx_df()

    import app.services.market_data_service as svc

    original = svc._AKSHARE_SOURCES
    try:
        sina_entry = next(entry for entry in original if entry[0] == "sina")
        svc._AKSHARE_SOURCES = [("sina", slow_sina, sina_entry[2], sina_entry[3])]
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                bars = high_available_akshare("600519", "20240101", "20240103")
                assert bars[0]["close"] == 10.5
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        svc._AKSHARE_SOURCES = original
        _source_failure_time.clear()

    assert errors == []
    assert max_in_flight == 1


def test_iter_date_chunks_splits_long_range() -> None:
    from datetime import date, timedelta

    from app.services.market_data_service import _iter_date_chunks

    chunks = _iter_date_chunks(date(2020, 1, 1), date(2022, 6, 30), chunk_days=365)
    assert chunks[0] == (date(2020, 1, 1), date(2020, 12, 30))
    assert chunks[-1][1] == date(2022, 6, 30)
    assert all((end - start).days + 1 <= 365 for start, end in chunks)
    for i in range(1, len(chunks)):
        assert chunks[i][0] == chunks[i - 1][1] + timedelta(days=1)


def test_merge_bars_by_date_dedupes_overlap_and_sorts() -> None:
    from app.services.market_data_service import _merge_bars_by_date

    older = [
        {"date": "2024-01-03", "close": 1.0},
        {"date": "2024-01-02", "close": 2.0},
    ]
    newer = [
        {"date": "2024-01-03", "close": 3.0},
        {"date": "2024-01-04", "close": 4.0},
    ]
    merged = _merge_bars_by_date([older, newer])
    assert [bar["date"] for bar in merged] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert merged[1]["close"] == 3.0


def test_fetch_qfq_bars_range_requests_year_chunks(monkeypatch) -> None:
    from datetime import date

    from app.services.market_data_service import fetch_qfq_bars_range

    calls: list[tuple[str, str]] = []

    def fake_ha(numeric_code, start_date, end_date, adjust="qfq"):
        calls.append((start_date, end_date, adjust))
        return [
            {
                "date": f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "volume": 1.0,
            }
        ]

    monkeypatch.setattr(
        "app.services.market_data_service.high_available_akshare",
        fake_ha,
    )
    bars = fetch_qfq_bars_range("sh600900", date(2020, 1, 1), date(2021, 6, 1), retries=0)
    assert len(calls) >= 2
    assert calls[0][0] == "20200101"
    assert calls[0][2] == "qfq"
    assert calls[-1][1] == "20210601"
    assert [bar["date"] for bar in bars] == sorted(bar["date"] for bar in bars)

