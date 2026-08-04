from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

import akshare as ak
from pypinyin import Style, lazy_pinyin


@dataclass(slots=True)
class StockMeta:
    stock_code: str
    stock_name: str
    exchange: str
    short_code: str
    initials: str


_CACHE_TTL = timedelta(hours=12)
_cache_lock = Lock()
_cached_items: list[StockMeta] = []
_cached_at: datetime | None = None


def normalize_stock_code(raw: str) -> str:
    value = raw.strip().lower()
    if len(value) == 8 and value[:2] in {"sh", "sz", "bj"} and value[2:].isdigit():
        return value

    if len(value) == 6 and value.isdigit():
        first = value[0]
        if first in {"6", "9"}:
            return f"sh{value}"
        if first in {"0", "2", "3"}:
            return f"sz{value}"
        if first in {"4", "8"}:
            return f"bj{value}"

    raise ValueError("invalid stock code")


def _exchange_from_normalized(code: str) -> str:
    prefix = code[:2]
    if prefix == "sh":
        return "SH"
    if prefix == "sz":
        return "SZ"
    if prefix == "bj":
        return "BJ"
    return "UNKNOWN"


def _get_initials(name: str) -> str:
    letters = lazy_pinyin(name, style=Style.FIRST_LETTER, strict=False)
    return "".join(letters).upper()


def _is_cache_valid(now: datetime) -> bool:
    if _cached_at is None:
        return False
    return now - _cached_at < _CACHE_TTL


def _build_cache() -> list[StockMeta]:
    df = ak.stock_info_a_code_name()
    items: list[StockMeta] = []

    for row in df.itertuples(index=False):
        code = str(getattr(row, "code", "")).strip()
        name = str(getattr(row, "name", "")).strip()
        if not code or not name:
            continue

        try:
            normalized = normalize_stock_code(code)
        except ValueError:
            continue

        items.append(
            StockMeta(
                stock_code=normalized,
                stock_name=name,
                exchange=_exchange_from_normalized(normalized),
                short_code=normalized[2:],
                initials=_get_initials(name),
            )
        )

    return items


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
