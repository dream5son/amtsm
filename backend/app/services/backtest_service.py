"""Backtest job orchestration: create/dedup, run (worker), and query.

Reuses the shared :mod:`app.market_signal` rule core via ``backtest_engine.run_backtest``
and the same forward-adjusted (qfq) daily-bar cache (``daily_market_snapshots``)
the live engine writes to (design.md 关键点2/4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.connection import get_db
from app.db.models import BacktestJob, BacktestTrade, DailyMarketSnapshot, Watchlist
from app.engine.market_hours import SH_TZ
from app.market_signal import RiskParams
from app.schemas.strategy import (
    StockStrategyOverride as StockStrategyOverrideSchema,
    StrategyConfig as StrategyConfigSchema,
    TrailingLadderLevel,
    parse_trailing_ladder,
)
from app.services.backtest_engine import (
    BacktestResult,
    run_backtest,
    sanitize_bars_for_backtest,
)
from app.services.market_data_service import (
    MarketDataUnavailableError,
    StockDataFetchError,
    fetch_qfq_bars_range,
)
from app.services.stock_search_service import normalize_stock_code
from app.services.strategy_service import (
    get_override,
    get_strategy,
    resolve_params,
    update_override,
    update_strategy,
)

logger = logging.getLogger(__name__)

# "Since listing" sentinel for omitted start_date (story 08: "区间从该股票上市到
# 现在"); the data source naturally truncates to whatever history exists.
_EARLIEST_START = date(2000, 1, 1)

_INFLIGHT_STATUSES = ("PENDING", "RUNNING")

# Extra rows fetched before sanitize so a few dirty OHLC bars don't shorten a page.
_KLINE_LIMIT_OVERFETCH = 20

# Same threshold as backtest_engine: holes longer than this are fetched again,
# then treated as legitimate halts if still empty.
_MAX_SNAPSHOT_GAP_CALENDAR_DAYS = 30

_BACKTEST_PARAM_KEYS = (
    "n",
    "x",
    "y",
    "stop_loss_pct",
    "break_even_trigger_pct",
    "break_even_buffer_pct",
    "trailing_ladder",
)


class StockNotFoundError(ValueError):
    """Raised when stock_code is not in watchlist."""


class BacktestJobNotFoundError(ValueError):
    """Raised when a job_id does not exist."""


class BacktestDataError(Exception):
    """Raised when required market data cannot be assembled for a job."""


class BacktestApplyError(ValueError):
    """Raised when a job cannot be applied (wrong status / bad params)."""


def _require_watchlist(session, stock_code: str) -> None:
    exists = (
        session.query(Watchlist.stock_code).filter(Watchlist.stock_code == stock_code).first()
    )
    if not exists:
        raise StockNotFoundError(f"stock not found in watchlist: {stock_code}")


def _resolve_backtest_params(stock_code: str, overrides: dict) -> dict:
    """Merge ``overrides`` onto the stock's current effective strategy params."""
    base = resolve_params(stock_code)
    merged: dict = {}
    for key in _BACKTEST_PARAM_KEYS:
        override_value = overrides.get(key)
        merged[key] = override_value if override_value is not None else base[key]

    ladder = parse_trailing_ladder(merged["trailing_ladder"])
    merged["trailing_ladder"] = [level.model_dump() for level in ladder]
    merged["n"] = int(merged["n"])
    merged["x"] = float(merged["x"])
    merged["y"] = float(merged["y"])
    merged["stop_loss_pct"] = float(merged["stop_loss_pct"])
    merged["break_even_trigger_pct"] = float(merged["break_even_trigger_pct"])
    merged["break_even_buffer_pct"] = float(merged["break_even_buffer_pct"])
    return merged


def _hash_params(resolved: dict) -> str:
    canonical = json.dumps(resolved, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _risk_params_from_resolved(resolved: dict) -> RiskParams:
    return RiskParams(
        stop_loss_pct=float(resolved["stop_loss_pct"]),
        break_even_trigger_pct=float(resolved["break_even_trigger_pct"]),
        break_even_buffer_pct=float(resolved["break_even_buffer_pct"]),
        trailing_ladder=parse_trailing_ladder(resolved["trailing_ladder"]),
        enable_partial_take_profit=False,
    )


def _job_to_dict(job: BacktestJob) -> dict:
    return {
        "id": job.id,
        "stock_code": job.stock_code,
        "status": job.status,
        "start_date": job.start_date,
        "end_date": job.end_date,
        "params_json": job.params_json,
        "params_hash": job.params_hash,
        "compare_group_id": job.compare_group_id,
        "win_rate": job.win_rate,
        "avg_win_loss_ratio": job.avg_win_loss_ratio,
        "max_drawdown": job.max_drawdown,
        "trade_count": job.trade_count,
        "total_return": job.total_return,
        "annual_return": job.annual_return,
        "sample_insufficient": bool(job.sample_insufficient),
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _params_snapshot_to_override_payload(
    params: dict,
    *,
    existing_override: dict | None,
) -> StockStrategyOverrideSchema:
    """Map backtest params_json keys onto StockStrategyOverride fields.

    Preserves existing enable_* overrides; backtest snapshots do not carry switches.
    """
    ladder_raw = params.get("trailing_ladder")
    ladder = (
        [TrailingLadderLevel.model_validate(level) for level in ladder_raw]
        if ladder_raw is not None
        else None
    )
    if ladder is not None:
        ladder = parse_trailing_ladder(ladder)

    prev = existing_override or {}
    return StockStrategyOverrideSchema(
        custom_n=int(params["n"]),
        custom_x=float(params["x"]),
        custom_y=float(params["y"]),
        stop_loss_pct=float(params["stop_loss_pct"]),
        break_even_trigger_pct=float(params["break_even_trigger_pct"]),
        break_even_buffer_pct=float(params["break_even_buffer_pct"]),
        trailing_ladder=ladder,
        enable_partial_take_profit=prev.get("enable_partial_take_profit"),
        enable_addon_alert=prev.get("enable_addon_alert"),
        enable_tech_sell_while_holding=prev.get("enable_tech_sell_while_holding"),
    )


def apply_job_params(job_id: int, *, update_global: bool = False) -> dict:
    """Write a SUCCESS job's params snapshot to the stock's single-stock override.

    Optionally also overlays the same numeric fields onto the global strategy.
    Idempotent: re-applying the same snapshot yields the same override.
    """
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None:
            raise BacktestJobNotFoundError(f"backtest job not found: {job_id}")
        if job.status != "SUCCESS":
            raise BacktestApplyError(
                f"only SUCCESS jobs can be applied (status={job.status})"
            )
        stock_code = job.stock_code
        try:
            params = json.loads(job.params_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BacktestApplyError("job params_json is invalid") from exc
        if not isinstance(params, dict):
            raise BacktestApplyError("job params_json must be an object")
        for key in _BACKTEST_PARAM_KEYS:
            if key not in params:
                raise BacktestApplyError(f"job params_json missing field: {key}")

    try:
        existing = get_override(stock_code)
    except ValueError as exc:
        raise StockNotFoundError(str(exc)) from exc

    override_payload = _params_snapshot_to_override_payload(params, existing_override=existing)
    override = update_override(stock_code, override_payload)

    global_strategy = None
    if update_global:
        current = get_strategy()
        ladder = parse_trailing_ladder(params["trailing_ladder"])
        global_payload = StrategyConfigSchema(
            global_buy_n=int(params["n"]),
            global_buy_x=float(params["x"]),
            global_sell_n=int(params["n"]),
            global_sell_y=float(params["y"]),
            stop_loss_pct=float(params["stop_loss_pct"]),
            break_even_trigger_pct=float(params["break_even_trigger_pct"]),
            break_even_buffer_pct=float(params["break_even_buffer_pct"]),
            trailing_ladder=ladder,
            enable_partial_take_profit=bool(current["enable_partial_take_profit"]),
            enable_addon_alert=bool(current["enable_addon_alert"]),
            enable_tech_sell_while_holding=bool(
                current["enable_tech_sell_while_holding"]
            ),
        )
        global_strategy = update_strategy(global_payload)

    return {
        "stock_code": stock_code,
        "override": override,
        "updated_global": bool(update_global),
        "global_strategy": global_strategy,
    }


def create_jobs(
    stock_code: str,
    start_date_value: date | None,
    end_date_value: date | None,
    params_list: list[dict] | None,
) -> dict:
    """Create (or dedup-reuse) one BacktestJob per params group.

    All jobs created in a single call share a newly generated
    ``compare_group_id``; jobs reused via dedup keep their own existing group.
    """
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        _require_watchlist(session, normalized)

    end = end_date_value or datetime.now(SH_TZ).date()
    start = start_date_value or _EARLIEST_START
    if start >= end:
        raise ValueError("start_date must be before end_date")

    groups = params_list or [{}]
    if not groups:
        groups = [{}]

    compare_group_id = str(uuid.uuid4())
    jobs: list[dict] = []

    for raw_overrides in groups:
        resolved = _resolve_backtest_params(normalized, raw_overrides)
        params_hash = _hash_params(resolved)

        with get_db() as session:
            existing = (
                session.query(BacktestJob)
                .filter(
                    BacktestJob.stock_code == normalized,
                    BacktestJob.params_hash == params_hash,
                    BacktestJob.start_date == start.isoformat(),
                    BacktestJob.end_date == end.isoformat(),
                    BacktestJob.status.in_(_INFLIGHT_STATUSES),
                )
                .order_by(BacktestJob.created_at.desc())
                .first()
            )
            if existing is not None:
                jobs.append(_job_to_dict(existing))
                continue

            job = BacktestJob(
                stock_code=normalized,
                status="PENDING",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                params_json=json.dumps(resolved, separators=(",", ":")),
                params_hash=params_hash,
                compare_group_id=compare_group_id,
                sample_insufficient=0,
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Concurrent request beat us to the partial unique index — reuse it.
                existing = (
                    session.query(BacktestJob)
                    .filter(
                        BacktestJob.stock_code == normalized,
                        BacktestJob.params_hash == params_hash,
                        BacktestJob.start_date == start.isoformat(),
                        BacktestJob.end_date == end.isoformat(),
                        BacktestJob.status.in_(_INFLIGHT_STATUSES),
                    )
                    .first()
                )
                if existing is None:
                    raise
                jobs.append(_job_to_dict(existing))
                continue
            jobs.append(_job_to_dict(job))

    return {"compare_group_id": compare_group_id, "jobs": jobs}


def get_job(job_id: int) -> dict:
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None:
            raise BacktestJobNotFoundError(f"backtest job not found: {job_id}")
        return _job_to_dict(job)


def list_jobs_by_stock(stock_code: str) -> list[dict]:
    normalized = normalize_stock_code(stock_code)
    with get_db() as session:
        rows = (
            session.query(BacktestJob)
            .filter(BacktestJob.stock_code == normalized)
            .order_by(BacktestJob.created_at.desc())
            .all()
        )
        return [_job_to_dict(row) for row in rows]


def list_jobs_by_compare_group(compare_group_id: str) -> list[dict]:
    with get_db() as session:
        rows = (
            session.query(BacktestJob)
            .filter(BacktestJob.compare_group_id == compare_group_id)
            .order_by(BacktestJob.created_at.asc())
            .all()
        )
        return [_job_to_dict(row) for row in rows]


def _iso_date(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return value


def _calendar_buffer_start(end: date, bar_count: int) -> date:
    """Calendar lookback covering roughly ``bar_count`` trading days."""
    return end - timedelta(days=bar_count * 3 + 30)


def _kline_fetch_window(
    job_start: str,
    job_end: str,
    *,
    start_iso: str | None,
    end_iso: str | None,
    before_iso: str | None,
    after_iso: str | None,
    limit: int | None,
) -> tuple[date, date]:
    """Date window to backfill before serving one kline page."""
    job_s = date.fromisoformat(job_start)
    job_e = date.fromisoformat(job_end)
    if start_iso is not None or end_iso is not None:
        win_s = date.fromisoformat(start_iso) if start_iso else job_s
        win_e = date.fromisoformat(end_iso) if end_iso else job_e
    elif before_iso is not None and limit is not None:
        win_e = date.fromisoformat(before_iso) - timedelta(days=1)
        win_s = _calendar_buffer_start(win_e, limit)
    elif after_iso is not None and limit is not None:
        win_s = date.fromisoformat(after_iso) + timedelta(days=1)
        win_e = win_s + timedelta(days=limit * 3 + 30)
    elif limit is not None:
        win_e = job_e
        win_s = _calendar_buffer_start(win_e, limit)
    else:
        win_s, win_e = job_s, job_e
    return max(win_s, job_s), min(win_e, job_e)


def _backfill_kline_window(stock_code: str, start: date, end: date, job_id: int) -> None:
    if start > end:
        return
    try:
        _ensure_snapshot_range(stock_code, start, end)
    except (MarketDataUnavailableError, StockDataFetchError, BacktestDataError) as exc:
        logger.warning(
            "kline backfill failed job=%s stock=%s range=%s..%s: %s",
            job_id,
            stock_code,
            start.isoformat(),
            end.isoformat(),
            exc,
        )


def get_kline_data(
    job_id: int,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    before: date | str | None = None,
    after: date | str | None = None,
    limit: int | None = None,
    include_trades: bool = True,
) -> dict:
    if before is not None and after is not None:
        raise ValueError("provide either before or after, not both")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")

    start_iso = _iso_date(start)
    end_iso = _iso_date(end)
    before_iso = _iso_date(before)
    after_iso = _iso_date(after)

    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None:
            raise BacktestJobNotFoundError(f"backtest job not found: {job_id}")
        stock_code = job.stock_code
        job_start = job.start_date
        job_end = job.end_date
        job_payload = _job_to_dict(job)

    fetch_start, fetch_end = _kline_fetch_window(
        job_start,
        job_end,
        start_iso=start_iso,
        end_iso=end_iso,
        before_iso=before_iso,
        after_iso=after_iso,
        limit=limit,
    )
    _backfill_kline_window(stock_code, fetch_start, fetch_end, job_id)

    with get_db() as session:
        win_start = job_start
        win_end = job_end
        if start_iso is not None:
            win_start = max(win_start, start_iso)
        if end_iso is not None:
            win_end = min(win_end, end_iso)

        conds = [
            DailyMarketSnapshot.stock_code == stock_code,
            DailyMarketSnapshot.trade_date >= win_start,
            DailyMarketSnapshot.trade_date <= win_end,
        ]
        if before_iso is not None:
            conds.append(DailyMarketSnapshot.trade_date < before_iso)
        if after_iso is not None:
            conds.append(DailyMarketSnapshot.trade_date > after_iso)

        # Tail pages (init / before / limit-only) take the newest N; after takes
        # the oldest N so the chart can append newer bars on the right.
        take_head = after_iso is not None
        stmt = select(DailyMarketSnapshot).where(*conds)
        if limit is not None:
            fetch_n = limit + _KLINE_LIMIT_OVERFETCH
            if take_head:
                stmt = stmt.order_by(DailyMarketSnapshot.trade_date.asc()).limit(fetch_n)
                bar_rows = list(session.execute(stmt).scalars().all())
            else:
                stmt = stmt.order_by(DailyMarketSnapshot.trade_date.desc()).limit(fetch_n)
                bar_rows = list(reversed(session.execute(stmt).scalars().all()))
        else:
            stmt = stmt.order_by(DailyMarketSnapshot.trade_date.asc())
            bar_rows = list(session.execute(stmt).scalars().all())

        trade_rows = []
        if include_trades:
            trade_rows = (
                session.execute(
                    select(BacktestTrade)
                    .where(BacktestTrade.job_id == job_id)
                    .order_by(BacktestTrade.entry_date)
                )
                .scalars()
                .all()
            )

        # Mirror run_backtest's sanitize_bars_for_backtest so the chart never
        # shows a candle excluded from the simulation (negative qfq or
        # extreme intraday range that would poison low_min).
        raw_bars = [
            {
                "date": row.trade_date,
                "open": row.open_price,
                "high": row.high_price,
                "low": row.low_price,
                "close": row.close_price,
                "volume": row.volume,
            }
            for row in bar_rows
        ]
        cleaned = sanitize_bars_for_backtest(raw_bars)
        if limit is not None:
            cleaned = cleaned[:limit] if take_head else cleaned[-limit:]

        is_full_dump = (
            limit is None
            and before_iso is None
            and after_iso is None
            and win_start == job_start
            and win_end == job_end
        )
        if is_full_dump or not cleaned:
            has_more_before = False
            has_more_after = False
        else:
            has_more_before = cleaned[0]["date"] > job_start
            has_more_after = cleaned[-1]["date"] < job_end
            if limit is not None and len(cleaned) < limit:
                if take_head:
                    has_more_after = False
                else:
                    has_more_before = False

        return {
            "job": job_payload,
            "bars": [
                {
                    "trade_date": bar["date"],
                    "open_price": bar["open"],
                    "high_price": bar["high"],
                    "low_price": bar["low"],
                    "close_price": bar["close"],
                    "volume": bar.get("volume") if bar.get("volume") is not None else 0.0,
                }
                for bar in cleaned
            ],
            "trades": [
                {
                    "id": row.id,
                    "entry_date": row.entry_date,
                    "entry_price": row.entry_price,
                    "exit_date": row.exit_date,
                    "exit_price": row.exit_price,
                    "hold_days": row.hold_days,
                    "pnl_pct": row.pnl_pct,
                    "pnl_amount": row.pnl_amount,
                    "exit_reason": row.exit_reason,
                }
                for row in trade_rows
            ],
            "has_more_before": has_more_before,
            "has_more_after": has_more_after,
        }


def _warmup_start(start: date, n: int) -> date:
    # Calendar-day buffer covering >= n trading days, matching the heuristic
    # already used by market_data_service.fetch_daily_bars.
    return start - timedelta(days=n * 3 + 30)


def _snapshot_trade_dates(session, stock_code: str, start: date, end: date) -> list[str]:
    return list(
        session.execute(
            select(DailyMarketSnapshot.trade_date).where(
                DailyMarketSnapshot.stock_code == stock_code,
                DailyMarketSnapshot.trade_date >= start.isoformat(),
                DailyMarketSnapshot.trade_date <= end.isoformat(),
            ).order_by(DailyMarketSnapshot.trade_date)
        ).scalars().all()
    )


def _coverage_holes(
    trade_dates: list[str],
    start: date,
    end: date,
    *,
    max_gap_days: int = _MAX_SNAPSHOT_GAP_CALENDAR_DAYS,
) -> list[tuple[date, date]]:
    """Inclusive calendar holes longer than ``max_gap_days`` inside ``[start, end]``."""
    if start > end:
        return []
    if not trade_dates:
        return [(start, end)]
    dates = [date.fromisoformat(value) for value in trade_dates]
    holes: list[tuple[date, date]] = []
    if (dates[0] - start).days > max_gap_days:
        holes.append((start, dates[0] - timedelta(days=1)))
    for i in range(1, len(dates)):
        prev, nxt = dates[i - 1], dates[i]
        if (nxt - prev).days > max_gap_days:
            hole_start = prev + timedelta(days=1)
            hole_end = nxt - timedelta(days=1)
            if hole_start <= hole_end:
                holes.append((hole_start, hole_end))
    if (end - dates[-1]).days > max_gap_days:
        holes.append((dates[-1] + timedelta(days=1), end))
    return holes


def _has_sufficient_snapshot_coverage(session, stock_code: str, start: date, end: date) -> bool:
    trade_dates = _snapshot_trade_dates(session, stock_code, start, end)
    calendar_days = (end - start).days + 1
    expected_min = max(1, int(calendar_days * 0.5))
    if len(trade_dates) < expected_min:
        return False
    return not _coverage_holes(trade_dates, start, end)


def _fetch_and_upsert_range(stock_code: str, start: date, end: date) -> int:
    if start > end:
        return 0
    bars = fetch_qfq_bars_range(
        stock_code,
        start,
        end,
        retries=settings.backtest_snapshot_fetch_retries,
        retry_backoff_seconds=settings.backtest_snapshot_fetch_retry_backoff_seconds,
    )
    if not bars:
        return 0
    from app.engine.tasks import upsert_qfq_bars

    written = upsert_qfq_bars(stock_code, bars)
    logger.info(
        "backtest job fetched %d bars stock=%s range=%s..%s",
        written,
        stock_code,
        start.isoformat(),
        end.isoformat(),
    )
    return written


def _ensure_snapshot_range(stock_code: str, start: date, end: date) -> None:
    with get_db() as session:
        trade_dates = _snapshot_trade_dates(session, stock_code, start, end)
        calendar_days = (end - start).days + 1
        expected_min = max(1, int(calendar_days * 0.5))
        count_ok = len(trade_dates) >= expected_min
        holes = _coverage_holes(trade_dates, start, end)

    if count_ok and not holes:
        logger.info(
            "backtest job cache hit stock=%s range=%s..%s",
            stock_code,
            start.isoformat(),
            end.isoformat(),
        )
        return

    fetch_ranges = [(start, end)] if not count_ok or not trade_dates else holes
    logger.info(
        "backtest job fetching market data stock=%s range=%s..%s windows=%s",
        stock_code,
        start.isoformat(),
        end.isoformat(),
        ",".join(f"{a.isoformat()}..{b.isoformat()}" for a, b in fetch_ranges),
    )
    written = 0
    for window_start, window_end in fetch_ranges:
        written += _fetch_and_upsert_range(stock_code, window_start, window_end)

    with get_db() as session:
        remaining = _snapshot_trade_dates(session, stock_code, start, end)
    if not remaining:
        raise BacktestDataError(
            f"no market data available for {stock_code} in [{start}, {end}]"
        )
    leftover = _coverage_holes(remaining, start, end)
    if leftover:
        logger.info(
            "backtest job treating %d remaining hole(s) as halt/listing gaps stock=%s",
            len(leftover),
            stock_code,
        )
    elif written == 0 and count_ok:
        logger.info(
            "backtest job cache hit after hole check stock=%s range=%s..%s",
            stock_code,
            start.isoformat(),
            end.isoformat(),
        )


def _load_bars(stock_code: str, start: date, end: date) -> list[dict]:
    with get_db() as session:
        rows = session.execute(
            select(DailyMarketSnapshot)
            .where(
                DailyMarketSnapshot.stock_code == stock_code,
                DailyMarketSnapshot.trade_date >= start.isoformat(),
                DailyMarketSnapshot.trade_date <= end.isoformat(),
            )
            .order_by(DailyMarketSnapshot.trade_date)
        ).scalars().all()
    return [
        {
            "date": row.trade_date,
            "open": row.open_price,
            "high": row.high_price,
            "low": row.low_price,
            "close": row.close_price,
            "volume": row.volume,
        }
        for row in rows
    ]


def _mark_failed(job_id: int, message: str) -> None:
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None:
            return
        job.status = "FAILED"
        job.error_message = message[:2000]
        job.finished_at = datetime.now(SH_TZ)
        session.commit()


def _persist_result(job_id: int, result: BacktestResult) -> None:
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None:
            return
        session.query(BacktestTrade).filter(BacktestTrade.job_id == job_id).delete()
        for trade in result.trades:
            session.add(
                BacktestTrade(
                    job_id=job_id,
                    entry_date=trade.entry_date,
                    entry_price=trade.entry_price,
                    exit_date=trade.exit_date,
                    exit_price=trade.exit_price,
                    hold_days=trade.hold_days,
                    pnl_pct=trade.pnl_pct,
                    pnl_amount=trade.pnl_amount,
                    exit_reason=trade.exit_reason,
                )
            )
        job.status = "SUCCESS"
        job.win_rate = result.summary.win_rate
        job.avg_win_loss_ratio = result.summary.avg_win_loss_ratio
        job.max_drawdown = result.summary.max_drawdown
        job.trade_count = result.summary.trade_count
        job.total_return = result.summary.total_return
        job.annual_return = result.summary.annual_return
        job.sample_insufficient = 1 if result.summary.sample_insufficient else 0
        job.error_message = None
        job.finished_at = datetime.now(SH_TZ)
        session.commit()


def run_job(job_id: int) -> None:
    """Run one PENDING job end-to-end. Safe to call directly in tests.

    On failure, marks the job FAILED with ``error_message`` and leaves any
    prior SUCCESS result (from an older job with different params/dates)
    untouched — this function only ever mutates ``job_id`` itself.
    """
    with get_db() as session:
        job = session.get(BacktestJob, job_id)
        if job is None or job.status != "PENDING":
            return
        job.status = "RUNNING"
        session.commit()
        stock_code = job.stock_code
        start_date_str = job.start_date
        end_date_str = job.end_date
        params = json.loads(job.params_json)

    logger.info(
        "backtest job %s started stock=%s range=%s..%s n=%s x=%s",
        job_id,
        stock_code,
        start_date_str,
        end_date_str,
        params.get("n"),
        params.get("x"),
    )
    try:
        n = int(params["n"])
        x = float(params["x"])
        risk = _risk_params_from_resolved(params)

        start = date.fromisoformat(start_date_str)
        end = date.fromisoformat(end_date_str)
        warmup_start = _warmup_start(start, n)

        _ensure_snapshot_range(stock_code, warmup_start, end)
        bars = _load_bars(stock_code, warmup_start, end)
        if not bars:
            raise BacktestDataError(
                f"no cached market data for {stock_code} in [{warmup_start}, {end}]"
            )
        logger.info(
            "backtest job %s loaded %d bars stock=%s warmup=%s..%s",
            job_id,
            len(bars),
            stock_code,
            warmup_start.isoformat(),
            end.isoformat(),
        )

        result = run_backtest(
            bars,
            start_date=start_date_str,
            n=n,
            x=x,
            risk=risk,
            min_trade_count=settings.backtest_min_trade_count,
        )
        _persist_result(job_id, result)
        logger.info(
            "backtest job %s SUCCESS stock=%s win_rate=%s trade_count=%s "
            "total_return=%s sample_insufficient=%s effective_start=%s "
            "qfq_prefix_skipped=%s",
            job_id,
            stock_code,
            result.summary.win_rate,
            result.summary.trade_count,
            result.summary.total_return,
            result.summary.sample_insufficient,
            result.summary.effective_start_date,
            result.summary.qfq_prefix_skipped,
        )
    except (MarketDataUnavailableError, StockDataFetchError, BacktestDataError) as exc:
        logger.warning("backtest job %s failed: %s", job_id, exc)
        _mark_failed(job_id, str(exc))
    except Exception as exc:
        logger.exception("backtest job %s failed unexpectedly", job_id)
        _mark_failed(job_id, str(exc))


def _empty_summary() -> dict:
    return {
        "backtest_status": "NONE",
        "backtest_job_id": None,
        "backtest_win_rate": None,
        "backtest_trade_count": None,
        "backtest_sample_insufficient": False,
        "backtest_stale": False,
        "backtest_error_message": None,
    }


def get_watchlist_backtest_summary(stock_codes: list[str]) -> dict[str, dict]:
    """Batch-compute the backtest ring/status summary for each stock (avoids N+1).

    Precedence: an in-flight (PENDING/RUNNING) job always wins (shows progress);
    otherwise prefer the most recent SUCCESS job whose params_hash matches the
    stock's *current* effective params, falling back to the most recent SUCCESS
    job at all (marked ``backtest_stale``); if there is no SUCCESS job at all but
    the latest job FAILED, surface the failure without inventing a win_rate.
    """
    if not stock_codes:
        return {}

    with get_db() as session:
        rows = session.execute(
            select(BacktestJob)
            .where(BacktestJob.stock_code.in_(stock_codes))
            .order_by(BacktestJob.created_at.desc())
        ).scalars().all()

    by_stock: dict[str, list[BacktestJob]] = {}
    for row in rows:
        by_stock.setdefault(row.stock_code, []).append(row)

    result: dict[str, dict] = {}
    for code in stock_codes:
        jobs = by_stock.get(code)
        if not jobs:
            result[code] = _empty_summary()
            continue

        try:
            current_hash = _hash_params(_resolve_backtest_params(code, {}))
        except Exception:  # noqa: BLE001 - never break the whole list over one stock
            current_hash = None

        latest = jobs[0]
        in_flight = latest if latest.status in _INFLIGHT_STATUSES else None
        success_matching = next(
            (j for j in jobs if j.status == "SUCCESS" and j.params_hash == current_hash),
            None,
        )
        success_any = next((j for j in jobs if j.status == "SUCCESS"), None)
        chosen_success = success_matching or success_any
        latest_failed = latest if latest.status == "FAILED" else None

        if in_flight is not None:
            status = in_flight.status
            job_id = in_flight.id
        elif chosen_success is not None:
            status = "SUCCESS"
            job_id = chosen_success.id
        elif latest_failed is not None:
            status = "FAILED"
            job_id = latest_failed.id
        else:
            status = latest.status
            job_id = latest.id

        result[code] = {
            "backtest_status": status,
            "backtest_job_id": job_id,
            "backtest_win_rate": chosen_success.win_rate if chosen_success else None,
            "backtest_trade_count": chosen_success.trade_count if chosen_success else None,
            "backtest_sample_insufficient": (
                bool(chosen_success.sample_insufficient) if chosen_success else False
            ),
            "backtest_stale": bool(chosen_success and success_matching is None),
            "backtest_error_message": latest_failed.error_message if latest_failed else None,
        }

    return result
