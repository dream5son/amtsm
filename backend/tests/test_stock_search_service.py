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
