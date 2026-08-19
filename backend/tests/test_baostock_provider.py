from datetime import date
from types import SimpleNamespace

import pytest

from app.services.market_data.baostock_provider import (
    BaostockMarketDataProvider,
    from_baostock_code,
    to_baostock_code,
)
from app.services.market_data.base import MarketDataUnavailableError, StockMeta
from app.services.market_data.failover import FailoverMarketDataProvider


class _FakeResult:
    def __init__(
        self,
        *,
        error_code: str = "0",
        error_msg: str = "success",
        fields: list[str] | None = None,
        rows: list[list[str]] | None = None,
    ) -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = fields or []
        self._rows = list(rows or [])
        self._idx = -1

    def next(self) -> bool:
        self._idx += 1
        return self._idx < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._idx]


class _FakeBaostock:
    def __init__(self) -> None:
        self.login_calls = 0
        self.k_calls: list[dict] = []
        self.basic_calls = 0
        self.login_result = SimpleNamespace(error_code="0", error_msg="success")
        self.k_result = _FakeResult()
        self.basic_result = _FakeResult()
        self.login_side_effect: BaseException | None = None

    def login(self):
        self.login_calls += 1
        if self.login_side_effect is not None:
            raise self.login_side_effect
        return self.login_result

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.k_calls.append({"code": code, "fields": fields, **kwargs})
        return self.k_result

    def query_stock_basic(self, code=None, code_name=None):
        self.basic_calls += 1
        return self.basic_result


def _daily_result(rows: list[list[str]] | None = None, **kwargs) -> _FakeResult:
    return _FakeResult(
        fields=["date", "open", "high", "low", "close", "volume", "turn"],
        rows=rows
        if rows is not None
        else [["2024-01-02", "10.0", "11.0", "9.5", "10.5", "1000", "3.14"]],
        **kwargs,
    )


def test_to_baostock_code_formats_internal_and_numeric() -> None:
    assert to_baostock_code("sh600519") == "sh.600519"
    assert to_baostock_code("600519") == "sh.600519"
    assert to_baostock_code("sh.600519") == "sh.600519"
    assert to_baostock_code("000001") == "sz.000001"
    assert from_baostock_code("sh.600519") == "sh600519"


def test_fetch_daily_ohlcv_maps_code_and_adjustflag() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result()
    provider = BaostockMarketDataProvider(client=client)

    bars = provider.fetch_daily_ohlcv(
        "sh600519", date(2024, 1, 1), date(2024, 1, 3), adjust="qfq"
    )

    assert len(bars) == 1
    assert bars[0]["close"] == 10.5
    assert bars[0]["turnover_rate"] == pytest.approx(3.14)
    assert client.login_calls == 1
    assert client.k_calls[0]["code"] == "sh.600519"
    assert client.k_calls[0]["adjustflag"] == "2"
    assert client.k_calls[0]["start_date"] == "2024-01-01"
    assert client.k_calls[0]["frequency"] == "d"


def test_fetch_daily_ohlcv_unadjusted_uses_flag_3() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result()
    provider = BaostockMarketDataProvider(client=client)

    provider.fetch_daily_ohlcv("600519", date(2024, 1, 1), date(2024, 1, 1), adjust="")

    assert client.k_calls[0]["adjustflag"] == "3"


def test_fetch_daily_ohlcv_error_code_raises_unavailable() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result(error_code="1", error_msg="network")
    provider = BaostockMarketDataProvider(client=client)

    with pytest.raises(MarketDataUnavailableError, match="baostock query failed"):
        provider.fetch_daily_ohlcv("600519", date(2024, 1, 1), date(2024, 1, 1))


def test_fetch_daily_ohlcv_empty_rows_returns_empty_list() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result(rows=[])
    provider = BaostockMarketDataProvider(client=client)

    assert (
        provider.fetch_daily_ohlcv("600519", date(2024, 1, 1), date(2024, 1, 1)) == []
    )


def test_fetch_daily_ohlcv_skips_incomplete_rows() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result(
        rows=[
            ["2024-01-02", "", "11.0", "9.5", "10.5", "1000", "1.0"],
            ["2024-01-03", "10.0", "11.0", "9.5", "10.5", "1000", ""],
        ]
    )
    provider = BaostockMarketDataProvider(client=client)
    bars = provider.fetch_daily_ohlcv("600519", date(2024, 1, 2), date(2024, 1, 3))
    assert [bar["date"] for bar in bars] == ["2024-01-03"]
    assert bars[0]["turnover_rate"] is None


def test_fetch_daily_ohlcv_skips_invalid_ohlc_not_bad_turnover() -> None:
    client = _FakeBaostock()
    client.k_result = _daily_result(
        rows=[
            ["2024-01-02", "oops", "11.0", "9.5", "10.5", "1000", "1.0"],
            ["2024-01-03", "10.0", "11.0", "9.5", "10.5", "1000", "oops-turn"],
        ]
    )
    provider = BaostockMarketDataProvider(client=client)
    bars = provider.fetch_daily_ohlcv("600519", date(2024, 1, 2), date(2024, 1, 3))
    assert [bar["date"] for bar in bars] == ["2024-01-03"]
    assert bars[0]["open"] == 10.0
    assert bars[0]["turnover_rate"] is None


def test_fetch_realtime_quotes_not_implemented() -> None:
    provider = BaostockMarketDataProvider(client=_FakeBaostock())
    with pytest.raises(NotImplementedError, match="realtime"):
        provider.fetch_realtime_quotes(["sh600519"])


def test_list_a_share_universe_filters_index_and_delisted() -> None:
    client = _FakeBaostock()
    client.basic_result = _FakeResult(
        fields=["code", "code_name", "type", "status"],
        rows=[
            ["sh.600519", "贵州茅台", "1", "1"],
            ["sh.000001", "上证综指", "2", "1"],
            ["sz.000001", "平安银行", "1", "0"],
            ["sz.000002", "万  科Ａ", "1", "1"],
        ],
    )
    provider = BaostockMarketDataProvider(client=client)
    items = provider.list_a_share_universe()

    assert [item.stock_code for item in items] == ["sh600519", "sz000002"]
    assert items[0].exchange == "SH"
    assert items[0].initials == "GZMT"
    assert items[0].short_code == "600519"


def test_login_failure_raises_unavailable() -> None:
    client = _FakeBaostock()
    client.login_result = SimpleNamespace(error_code="10001011", error_msg="banned")
    provider = BaostockMarketDataProvider(client=client)
    with pytest.raises(MarketDataUnavailableError, match="login failed"):
        provider.fetch_daily_ohlcv("600519", date(2024, 1, 1), date(2024, 1, 1))


class _FakeDailyProvider:
    provider_id = "primary"

    def __init__(
        self, bars: list[dict] | None = None, error: BaseException | None = None
    ):
        self.bars = bars if bars is not None else []
        self.error = error
        self.ohlcv_calls = 0
        self.quote_calls = 0
        self.universe_calls = 0

    def fetch_daily_ohlcv(self, stock_code, start_date, end_date, *, adjust="qfq"):
        self.ohlcv_calls += 1
        if self.error is not None:
            raise self.error
        return self.bars

    def fetch_realtime_quotes(self, stock_codes, *, timeout_seconds=3.0):
        self.quote_calls += 1
        raise NotImplementedError("no realtime")

    def list_a_share_universe(self):
        self.universe_calls += 1
        if self.error is not None:
            raise self.error
        return []


class _FakeAkshare:
    provider_id = "akshare"

    def __init__(self) -> None:
        self.ohlcv_calls = 0
        self.quote_calls = 0
        self.universe_calls = 0

    def fetch_daily_ohlcv(self, stock_code, start_date, end_date, *, adjust="qfq"):
        self.ohlcv_calls += 1
        return [
            {
                "date": "2024-01-02",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "turnover_rate": None,
            }
        ]

    def fetch_realtime_quotes(self, stock_codes, *, timeout_seconds=3.0):
        self.quote_calls += 1
        return {
            stock_codes[0]: {
                "stock_name": "贵州茅台",
                "price": 101.0,
                "open": 100.0,
                "prev_close": 99.0,
                "high": 102.0,
                "low": 98.0,
                "volume": 100.0,
                "quote_date": "2024-01-02",
                "quote_time": "10:00:00",
                "is_halted": False,
                "has_quote": True,
            }
        }

    def list_a_share_universe(self):
        self.universe_calls += 1
        return [
            StockMeta(
                stock_code="sh600519",
                stock_name="贵州茅台",
                exchange="SH",
                short_code="600519",
                initials="GZMT",
            )
        ]


def test_failover_uses_primary_daily_without_calling_fallback() -> None:
    primary = _FakeDailyProvider(
        bars=[
            {
                "date": "2024-01-02",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1.0,
                "turnover_rate": None,
            }
        ]
    )
    fallback = _FakeAkshare()
    provider = FailoverMarketDataProvider([primary, fallback])

    bars = provider.fetch_daily_ohlcv("sh600519", date(2024, 1, 1), date(2024, 1, 2))
    assert bars[0]["close"] == 10.5
    assert primary.ohlcv_calls == 1
    assert fallback.ohlcv_calls == 0


def test_failover_does_not_fallback_on_empty_daily_bars() -> None:
    primary = _FakeDailyProvider(bars=[])
    fallback = _FakeAkshare()
    provider = FailoverMarketDataProvider([primary, fallback])

    assert (
        provider.fetch_daily_ohlcv("sh600519", date(2024, 1, 1), date(2024, 1, 2)) == []
    )
    assert fallback.ohlcv_calls == 0


def test_failover_daily_on_unavailable() -> None:
    primary = _FakeDailyProvider(error=MarketDataUnavailableError("down"))
    fallback = _FakeAkshare()
    provider = FailoverMarketDataProvider([primary, fallback])

    bars = provider.fetch_daily_ohlcv("sh600519", date(2024, 1, 1), date(2024, 1, 2))
    assert bars[0]["close"] == 1.0
    assert fallback.ohlcv_calls == 1


def test_failover_realtime_on_not_implemented() -> None:
    primary = _FakeDailyProvider()
    fallback = _FakeAkshare()
    provider = FailoverMarketDataProvider([primary, fallback])

    quotes = provider.fetch_realtime_quotes(["sh600519"])
    assert quotes["sh600519"]["price"] == 101.0
    assert fallback.quote_calls == 1


def test_failover_universe_on_unavailable() -> None:
    primary = _FakeDailyProvider(error=MarketDataUnavailableError("down"))
    fallback = _FakeAkshare()
    provider = FailoverMarketDataProvider([primary, fallback])

    items = provider.list_a_share_universe()
    assert items[0].stock_code == "sh600519"
    assert fallback.universe_calls == 1


def test_failover_raises_when_all_fail() -> None:
    primary = _FakeDailyProvider(error=MarketDataUnavailableError("down"))
    secondary = _FakeDailyProvider(error=NotImplementedError("nope"))
    provider = FailoverMarketDataProvider([primary, secondary])

    with pytest.raises(MarketDataUnavailableError, match="All market data providers"):
        provider.fetch_daily_ohlcv("sh600519", date(2024, 1, 1), date(2024, 1, 2))
