import pytest

from app.services.stock_search_service import (
    StockMeta,
    normalize_stock_code,
    search_stocks,
)


def test_normalize_stock_code_supports_main_board_prefixes() -> None:
    assert normalize_stock_code("600519") == "sh600519"
    assert normalize_stock_code("000001") == "sz000001"
    assert normalize_stock_code("430047") == "bj430047"
    assert normalize_stock_code("sh600519") == "sh600519"


def test_normalize_stock_code_supports_etf_prefixes() -> None:
    assert normalize_stock_code("159941") == "sz159941"
    assert normalize_stock_code("510300") == "sh510300"
    assert normalize_stock_code("588000") == "sh588000"
    assert normalize_stock_code("sz159941") == "sz159941"


def test_normalize_stock_code_rejects_unknown_prefixes() -> None:
    with pytest.raises(ValueError, match="invalid stock code"):
        normalize_stock_code("110059")


def test_search_stocks_ranks_code_and_initials(monkeypatch) -> None:
    items = [
        StockMeta(
            stock_code="sh600519",
            stock_name="贵州茅台",
            exchange="SH",
            short_code="600519",
            initials="GZMT",
        ),
        StockMeta(
            stock_code="sz000001",
            stock_name="平安银行",
            exchange="SZ",
            short_code="000001",
            initials="PAYH",
        ),
        StockMeta(
            stock_code="sh600000",
            stock_name="浦发银行",
            exchange="SH",
            short_code="600000",
            initials="PFYH",
        ),
    ]

    monkeypatch.setattr(
        "app.services.stock_search_service._load_stock_meta", lambda: items
    )

    by_code = search_stocks("600519")
    by_initials = search_stocks("GZMT")
    by_name = search_stocks("平安")

    assert by_code[0]["stock_code"] == "sh600519"
    assert by_initials[0]["stock_name"] == "贵州茅台"
    assert by_name[0]["stock_name"] == "平安银行"


def test_search_stocks_matches_etf_code_and_name(monkeypatch) -> None:
    items = [
        StockMeta(
            stock_code="sz159941",
            stock_name="纳指ETF广发",
            exchange="SZ",
            short_code="159941",
            initials="NZETFGF",
        ),
        StockMeta(
            stock_code="sh510300",
            stock_name="沪深300ETF",
            exchange="SH",
            short_code="510300",
            initials="HS300ETF",
        ),
    ]
    monkeypatch.setattr(
        "app.services.stock_search_service._load_stock_meta", lambda: items
    )

    by_code = search_stocks("159941")
    by_name = search_stocks("沪深300")

    assert by_code[0]["stock_code"] == "sz159941"
    assert by_name[0]["stock_code"] == "sh510300"


def test_build_cache_uses_provider_universe(monkeypatch) -> None:
    items = [
        StockMeta(
            stock_code="sh600519",
            stock_name="贵州茅台",
            exchange="SH",
            short_code="600519",
            initials="GZMT",
        )
    ]

    class _Fake:
        def list_a_share_universe(self):
            return items

    monkeypatch.setattr(
        "app.services.stock_search_service.get_market_data_provider",
        lambda: _Fake(),
    )
    from app.services.stock_search_service import _build_cache

    assert _build_cache() is items
