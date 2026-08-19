import threading
import time
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.services.market_data.akshare_provider import (
    _AKSHARE_SOURCES,
    AkshareMarketDataProvider,
    _code_em,
    _normalize_df_em,
    _parse_sina_realtime_quotes,
)
from app.services.market_data.base import (
    MarketDataProvider,
    MarketDataUnavailableError,
    StockMeta,
)
from app.services.market_data_service import (
    fetch_daily_bars,
    fetch_qfq_bars_range,
    fetch_realtime_quotes_batch,
    fetch_trade_day_bar,
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
    return pd.DataFrame(
        [
            {
                "日期": "2024-01-02",
                "开盘": 10.0,
                "最高": 11.0,
                "最低": 9.5,
                "收盘": 10.5,
                "成交量": 100000,
                "换手率": 3.14,
            }
        ]
    )


def _make_tx_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2024-01-02",
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 100000,
            }
        ]
    )


def _fetch(provider: AkshareMarketDataProvider, code: str = "600519"):
    return provider.fetch_daily_ohlcv(code, date(2024, 1, 1), date(2024, 1, 3))


def test_normalize_em_skips_nan_ohlc_and_coerces_bad_turnover() -> None:
    df = pd.DataFrame(
        [
            {
                "日期": "2024-01-02",
                "开盘": float("nan"),
                "最高": 11.0,
                "最低": 9.5,
                "收盘": 10.5,
                "成交量": 100000,
                "换手率": 3.14,
            },
            {
                "日期": "2024-01-03",
                "开盘": 10.0,
                "最高": 11.0,
                "最低": 9.5,
                "收盘": 10.5,
                "成交量": 100000,
                "换手率": "garbage",
            },
        ]
    )
    bars = _normalize_df_em(df)
    assert [bar["date"] for bar in bars] == ["2024-01-03"]
    assert bars[0]["close"] == 10.5
    assert bars[0]["turnover_rate"] is None


def test_akshare_provider_uses_first_source() -> None:
    mock_em = MagicMock(return_value=_make_em_df())
    provider = AkshareMarketDataProvider(
        sources=[("eastmoney", mock_em, _code_em, _normalize_df_em)]
    )
    bars = _fetch(provider)

    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    assert bars[0]["turnover_rate"] == pytest.approx(3.14)
    mock_em.assert_called_once()


def test_akshare_unusable_rows_fail_over_to_next_source() -> None:
    nan_df = pd.DataFrame(
        [
            {
                "日期": "2024-01-02",
                "开盘": float("nan"),
                "最高": float("nan"),
                "最低": float("nan"),
                "收盘": float("nan"),
                "成交量": float("nan"),
            }
        ]
    )
    mock_em = MagicMock(return_value=nan_df)
    mock_tx = MagicMock(return_value=_make_tx_df())
    provider = AkshareMarketDataProvider(
        sources=[
            ("eastmoney", mock_em, _AKSHARE_SOURCES[0][2], _AKSHARE_SOURCES[0][3]),
            ("tencent", mock_tx, _AKSHARE_SOURCES[1][2], _AKSHARE_SOURCES[1][3]),
        ]
    )
    bars = _fetch(provider)
    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    mock_tx.assert_called_once()
    assert "eastmoney" in provider._source_failure_time


def test_akshare_provider_falls_back_to_next_source() -> None:
    mock_em = MagicMock(side_effect=RuntimeError("network error"))
    mock_tx = MagicMock(return_value=_make_tx_df())
    provider = AkshareMarketDataProvider(
        sources=[
            ("eastmoney", mock_em, _AKSHARE_SOURCES[0][2], _AKSHARE_SOURCES[0][3]),
            ("tencent", mock_tx, _AKSHARE_SOURCES[1][2], _AKSHARE_SOURCES[1][3]),
        ]
    )
    bars = _fetch(provider)

    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    assert "eastmoney" in provider._source_failure_time


def test_akshare_provider_skips_source_in_cooldown() -> None:
    mock_em = MagicMock(return_value=_make_em_df())
    mock_tx = MagicMock(return_value=_make_tx_df())
    provider = AkshareMarketDataProvider(
        sources=[
            ("eastmoney", mock_em, _AKSHARE_SOURCES[0][2], _AKSHARE_SOURCES[0][3]),
            ("tencent", mock_tx, _AKSHARE_SOURCES[1][2], _AKSHARE_SOURCES[1][3]),
        ]
    )
    provider._source_failure_time["eastmoney"] = datetime.now(UTC)

    bars = _fetch(provider)

    mock_em.assert_not_called()
    mock_tx.assert_called_once()
    assert bars[0]["close"] == 10.5


def test_akshare_provider_raises_when_all_fail() -> None:
    mock_fn = MagicMock(side_effect=RuntimeError("dead"))
    provider = AkshareMarketDataProvider(
        sources=[
            ("src1", mock_fn, _code_em, _normalize_df_em),
            ("src2", mock_fn, _code_em, _normalize_df_em),
        ]
    )
    with pytest.raises(MarketDataUnavailableError):
        _fetch(provider)


def test_sina_source_calls_are_serialized() -> None:
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

    sina_entry = next(entry for entry in _AKSHARE_SOURCES if entry[0] == "sina")
    provider = AkshareMarketDataProvider(
        sources=[("sina", slow_sina, sina_entry[2], sina_entry[3])]
    )
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            bars = _fetch(provider)
            assert bars[0]["close"] == 10.5
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert max_in_flight == 1


def test_iter_date_chunks_splits_long_range() -> None:
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


class _FakeDailyProvider(MarketDataProvider):
    provider_id = "fake"

    def __init__(self) -> None:
        self.ohlcv_calls: list[tuple[str, date, date, str]] = []
        self.quote_calls = 0

    def fetch_daily_ohlcv(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        *,
        adjust: str = "qfq",
    ) -> list[dict]:
        self.ohlcv_calls.append((stock_code, start_date, end_date, adjust))
        return [
            {
                "date": start_date.isoformat(),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "volume": 1.0,
                "turnover_rate": None,
            }
        ]

    def fetch_realtime_quotes(
        self,
        stock_codes: list[str],
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, dict]:
        self.quote_calls += 1
        raise MarketDataUnavailableError("timed out")

    def list_a_share_universe(self) -> list[StockMeta]:
        return []


def test_fetch_qfq_bars_range_requests_year_chunks(monkeypatch) -> None:
    fake = _FakeDailyProvider()
    monkeypatch.setattr(
        "app.services.market_data_service.get_market_data_provider",
        lambda: fake,
    )
    bars = fetch_qfq_bars_range(
        "sh600900", date(2020, 1, 1), date(2021, 6, 1), retries=0
    )
    assert len(fake.ohlcv_calls) >= 2
    assert fake.ohlcv_calls[0][1] == date(2020, 1, 1)
    assert fake.ohlcv_calls[0][3] == "qfq"
    assert fake.ohlcv_calls[-1][2] == date(2021, 6, 1)
    assert [bar["date"] for bar in bars] == sorted(bar["date"] for bar in bars)


def test_orchestration_fetch_daily_bars_excludes_end_date(monkeypatch) -> None:
    fake = _FakeDailyProvider()
    monkeypatch.setattr(
        "app.services.market_data_service.get_market_data_provider",
        lambda: fake,
    )
    fetch_daily_bars("sh600519", 60, end_date=date(2026, 8, 5))
    assert len(fake.ohlcv_calls) == 1
    _, start, end, adjust = fake.ohlcv_calls[0]
    assert end == date(2026, 8, 4)
    assert start < end
    assert adjust == "qfq"


def test_orchestration_realtime_retry_without_akshare(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.market_data_service.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    fake = _FakeDailyProvider()
    monkeypatch.setattr(
        "app.services.market_data_service.get_market_data_provider",
        lambda: fake,
    )
    with pytest.raises(MarketDataUnavailableError):
        fetch_realtime_quotes_batch(
            ["sh600519"],
            timeout_seconds=0.1,
            retries=3,
            retry_backoff_seconds=0.5,
        )
    assert fake.quote_calls == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_fetch_trade_day_bar_uses_provider_range(monkeypatch) -> None:
    class _SingleBar(_FakeDailyProvider):
        def fetch_daily_ohlcv(self, stock_code, start_date, end_date, *, adjust="qfq"):
            super().fetch_daily_ohlcv(stock_code, start_date, end_date, adjust=adjust)
            return [
                {
                    "date": "2026-08-05",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "volume": 1000.0,
                    "turnover_rate": 2.5,
                }
            ]

    fake = _SingleBar()
    monkeypatch.setattr(
        "app.services.market_data_service.get_market_data_provider",
        lambda: fake,
    )
    bar = fetch_trade_day_bar("sh600519", date(2026, 8, 5), retries=0)
    assert bar["close"] == 10.5
    assert fake.ohlcv_calls[0] == (
        "sh600519",
        date(2026, 8, 5),
        date(2026, 8, 5),
        "qfq",
    )


def test_akshare_list_a_share_universe_normalizes_codes(monkeypatch) -> None:
    df = pd.DataFrame([{"code": "600519", "name": "贵州茅台"}])
    monkeypatch.setattr(
        "app.services.market_data.akshare_provider.ak.stock_info_a_code_name",
        lambda: df,
    )
    items = AkshareMarketDataProvider(sources=[]).list_a_share_universe()
    assert items[0].stock_code == "sh600519"
    assert items[0].exchange == "SH"
    assert items[0].initials == "GZMT"


def test_akshare_fetch_realtime_quotes_parses_http_body(monkeypatch) -> None:
    raw = (
        'var hq_str_sh600519="贵州茅台,100.00,99.00,101.00,102.00,98.00,101.00,101.01,100,1000,'
        '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-08-05,10:05:01,00";\n'
    )

    class _Resp:
        text = raw

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.services.market_data.akshare_provider.httpx.get",
        lambda *args, **kwargs: _Resp(),
    )
    quotes = AkshareMarketDataProvider(sources=[]).fetch_realtime_quotes(["sh600519"])
    assert quotes["sh600519"]["price"] == 101.0
    assert quotes["sh600519"]["has_quote"] is True
