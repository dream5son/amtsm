from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.connection import get_db
from app.db.models import DailyMarketSnapshot, StockYearRange
from app.engine.market_hours import SH_TZ
from app.services.market_data.numbers import coerce_optional_float

YEAR_RANGE_LOOKBACK_DAYS = 365


def year_range_since(trade_date: str) -> str:
    """Inclusive start date for the last calendar year ending on ``trade_date``."""
    return (
        date.fromisoformat(trade_date) - timedelta(days=YEAR_RANGE_LOOKBACK_DAYS)
    ).isoformat()


def compute_water_level(
    current_price: float, year_low: float, year_high: float
) -> float:
    """Return current price position in [year_low, year_high], clamped to 0..1."""
    span = year_high - year_low
    if span <= 0:
        return 0.5
    level = (current_price - year_low) / span
    if level < 0:
        return 0.0
    if level > 1:
        return 1.0
    return round(level, 6)


def year_high_lows_from_rows(
    rows: list[tuple[object, object, object]],
) -> dict[str, tuple[float, float]]:
    """Skip aggregate rows whose min/max came back empty."""
    result: dict[str, tuple[float, float]] = {}
    for code, low, high in rows:
        if code is None or low is None or high is None:
            continue
        result[str(code)] = (float(low), float(high))
    return result


def load_year_high_lows(codes: list[str], since: str) -> dict[str, tuple[float, float]]:
    if not codes:
        return {}
    with get_db() as session:
        rows = session.execute(
            select(
                DailyMarketSnapshot.stock_code,
                func.min(DailyMarketSnapshot.low_price),
                func.max(DailyMarketSnapshot.high_price),
            )
            .where(
                DailyMarketSnapshot.stock_code.in_(codes),
                DailyMarketSnapshot.trade_date >= since,
            )
            .group_by(DailyMarketSnapshot.stock_code)
        ).all()
    return year_high_lows_from_rows(list(rows))


def load_latest_closes(codes: list[str]) -> dict[str, float]:
    """Latest snapshot close_price per stock, skipping missing/non-positive values."""
    if not codes:
        return {}
    with get_db() as session:
        ranked = (
            select(
                DailyMarketSnapshot.stock_code,
                DailyMarketSnapshot.close_price,
                func.row_number()
                .over(
                    partition_by=DailyMarketSnapshot.stock_code,
                    order_by=DailyMarketSnapshot.trade_date.desc(),
                )
                .label("rn"),
            )
            .where(DailyMarketSnapshot.stock_code.in_(codes))
            .subquery()
        )
        rows = session.execute(
            select(ranked.c.stock_code, ranked.c.close_price).where(ranked.c.rn == 1)
        ).all()
    result: dict[str, float] = {}
    for code, close in rows:
        price = coerce_optional_float(close)
        if price is None or price <= 0:
            continue
        result[str(code)] = price
    return result


def upsert_year_ranges(rows: list[dict]) -> None:
    if not rows:
        return
    now = datetime.now(SH_TZ).replace(tzinfo=None)
    with get_db() as session:
        for row in rows:
            stmt = (
                sqlite_insert(StockYearRange)
                .values(
                    stock_code=row["stock_code"],
                    year_low=row["year_low"],
                    year_high=row["year_high"],
                    current_price=row["current_price"],
                    water_level=row["water_level"],
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["stock_code"],
                    set_={
                        "year_low": row["year_low"],
                        "year_high": row["year_high"],
                        "current_price": row["current_price"],
                        "water_level": row["water_level"],
                        "updated_at": now,
                    },
                )
            )
            session.execute(stmt)
        session.commit()


def refresh_year_range_stats(
    trade_date: str,
    quotes: dict[str, dict],
    codes: list[str],
) -> int:
    """Compute last-year high/low and water level from snapshots + live quotes.

    Persists current price, year_low, year_high, and water_level. Returns the
    number of rows written.
    """
    if not codes:
        return 0

    hist = load_year_high_lows(codes, year_range_since(trade_date))
    rows: list[dict] = []
    for code in codes:
        quote = quotes.get(code) or {}
        price = coerce_optional_float(quote.get("price"))
        if price is None or price <= 0:
            continue
        if quote.get("quote_date") != trade_date:
            continue

        quote_high = coerce_optional_float(quote.get("high"))
        quote_low = coerce_optional_float(quote.get("low"))
        hist_low, hist_high = hist.get(code, (price, price))
        year_low = hist_low
        year_high = hist_high
        if quote_low is not None and quote_low > 0:
            year_low = min(year_low, quote_low)
        if quote_high is not None and quote_high > 0:
            year_high = max(year_high, quote_high)
        year_low = min(year_low, price)
        year_high = max(year_high, price)
        if year_high < year_low:
            year_low, year_high = year_high, year_low

        rows.append(
            {
                "stock_code": code,
                "year_low": year_low,
                "year_high": year_high,
                "current_price": price,
                "water_level": compute_water_level(price, year_low, year_high),
            }
        )

    upsert_year_ranges(rows)
    return len(rows)


def refresh_year_range_from_snapshots(
    codes: list[str], *, as_of: str | None = None
) -> int:
    """Compute year range from recorded snapshots only (no live quote).

    ``current_price`` is the latest snapshot close. Returns rows written.
    """
    if not codes:
        return 0

    trade_date = as_of or datetime.now(SH_TZ).date().isoformat()
    hist = load_year_high_lows(codes, year_range_since(trade_date))
    closes = load_latest_closes(codes)
    rows: list[dict] = []
    for code in codes:
        price = closes.get(code)
        if price is None:
            continue
        hist_low, hist_high = hist.get(code, (price, price))
        year_low = min(hist_low, price)
        year_high = max(hist_high, price)
        if year_high < year_low:
            year_low, year_high = year_high, year_low
        rows.append(
            {
                "stock_code": code,
                "year_low": year_low,
                "year_high": year_high,
                "current_price": price,
                "water_level": compute_water_level(price, year_low, year_high),
            }
        )

    upsert_year_ranges(rows)
    return len(rows)
