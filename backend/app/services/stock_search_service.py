from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock

from app.services.market_data import (
    StockMeta,
    get_market_data_provider,
    normalize_stock_code,
)

_CACHE_TTL = timedelta(hours=12)
_cache_lock = Lock()
_cached_items: list[StockMeta] = []
_cached_at: datetime | None = None

__all__ = [
    "StockMeta",
    "normalize_stock_code",
    "search_stocks",
]


def _is_cache_valid(now: datetime) -> bool:
    if _cached_at is None:
        return False
    return now - _cached_at < _CACHE_TTL


def _build_cache() -> list[StockMeta]:
    return get_market_data_provider().list_a_share_universe()


def _load_stock_meta() -> list[StockMeta]:
    global _cached_at, _cached_items

    now = datetime.now(UTC)
    if _is_cache_valid(now):
        return _cached_items

    with _cache_lock:
        now = datetime.now(UTC)
        if _is_cache_valid(now):
            return _cached_items

        _cached_items = _build_cache()
        _cached_at = now
        return _cached_items


def search_stocks(query: str, limit: int = 20) -> list[dict]:
    keyword = query.strip()
    if not keyword:
        return []

    key_lower = keyword.lower()
    key_upper = keyword.upper()
    items = _load_stock_meta()

    normalized_exact = ""
    try:
        normalized_exact = normalize_stock_code(keyword)
    except ValueError:
        normalized_exact = ""

    def score(item: StockMeta) -> tuple[int, str]:
        if normalized_exact and item.stock_code == normalized_exact:
            return (100, item.stock_code)
        if item.short_code == keyword:
            return (95, item.stock_code)
        if item.short_code.startswith(keyword):
            return (85, item.stock_code)
        if item.initials == key_upper:
            return (80, item.stock_code)
        if item.initials.startswith(key_upper):
            return (75, item.stock_code)
        if key_upper in item.initials:
            return (65, item.stock_code)
        if item.stock_name == keyword:
            return (60, item.stock_code)
        if key_lower in item.stock_name.lower():
            return (50, item.stock_code)
        if key_lower in item.stock_code:
            return (45, item.stock_code)
        return (0, item.stock_code)

    matched: list[tuple[int, StockMeta]] = []
    for item in items:
        rank, _ = score(item)
        if rank > 0:
            matched.append((rank, item))

    matched.sort(key=lambda x: (-x[0], x[1].stock_code))

    result: list[dict] = []
    for _, item in matched[:limit]:
        result.append(
            {
                "stock_code": item.stock_code,
                "stock_name": item.stock_name,
                "exchange": item.exchange,
            }
        )
    return result
