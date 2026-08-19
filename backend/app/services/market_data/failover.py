"""Try market-data providers in order until one succeeds."""

from __future__ import annotations

import logging
from datetime import date

from app.services.market_data.base import (
    QFQ,
    DailyBar,
    MarketDataProvider,
    MarketDataUnavailableError,
    RealtimeQuote,
    StockMeta,
)

logger = logging.getLogger(__name__)

_FALLBACK_ERRORS = (NotImplementedError, MarketDataUnavailableError)


class FailoverMarketDataProvider(MarketDataProvider):
    """Prefer earlier providers; fall back on unimplemented or unavailable."""

    provider_id = "failover"

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = list(providers)

    def failover_targets(self) -> list[MarketDataProvider]:
        """Pin one inner provider for a whole-range fetch so chunks never mix sources."""
        return list(self._providers)

    def fetch_daily_ohlcv(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        *,
        adjust: str = QFQ,
    ) -> list[DailyBar]:
        return self._call(
            "fetch_daily_ohlcv",
            stock_code,
            start_date,
            end_date,
            adjust=adjust,
        )

    def fetch_realtime_quotes(
        self,
        stock_codes: list[str],
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, RealtimeQuote]:
        return self._call(
            "fetch_realtime_quotes",
            stock_codes,
            timeout_seconds=timeout_seconds,
        )

    def list_a_share_universe(self) -> list[StockMeta]:
        return self._call("list_a_share_universe")

    def _call(self, method_name: str, *args: object, **kwargs: object):
        last_exc: BaseException | None = None
        for provider in self._providers:
            method = getattr(provider, method_name)
            try:
                return method(*args, **kwargs)
            except _FALLBACK_ERRORS as exc:
                last_exc = exc
                logger.warning(
                    "provider '%s' %s failed, trying next: %s",
                    provider.provider_id,
                    method_name,
                    exc,
                )
        msg = str(last_exc) if last_exc else "no providers configured"
        raise MarketDataUnavailableError(
            f"All market data providers failed for {method_name}: {msg}"
        ) from last_exc
