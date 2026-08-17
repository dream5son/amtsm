from datetime import datetime


class RuntimeState:
    """In-memory state holder for baseline cache and per-day alert frequency control."""

    def __init__(self) -> None:
        self.baseline_cache: dict[str, dict] = {}
        self.signal_state: dict[str, str] = {}
        # Parallel to signal_state: UI hint flags (story 12 T+1 / limit board).
        self.signal_meta: dict[str, dict] = {}
        self.signal_trade_date: str | None = None
        self.sent_signal_keys: set[tuple[str, str, str]] = set()
        self.job_status: str = "IDLE"  # IDLE | RUNNING | FAILED | SUCCESS
        # User story 12: realtime quote resilience
        self.consecutive_poll_failures: int = 0
        self.quote_delay: bool = False
        self.quote_delay_since: str | None = None
        self.last_internal_alert_key: str | None = None
        self.no_baseline_warned_date: str | None = None
        # Throttle for folding intraday snapshot writes into market polling.
        self.last_intraday_snapshot_at: datetime | None = None
        # V2 risk: position snapshot cache + partial-TP ladder progress
        self.position_cache: dict[str, dict] = {}
        self.partial_tp_ladder_idx: dict[str, int] = {}
        self.exit_fired_today: set[str] = set()

    def reset_daily(self) -> None:
        self.baseline_cache.clear()
        self.signal_state.clear()
        self.signal_meta.clear()
        self.signal_trade_date = None
        self.sent_signal_keys.clear()
        self.partial_tp_ladder_idx.clear()
        self.exit_fired_today.clear()
        self.no_baseline_warned_date = None
        self.last_intraday_snapshot_at = None
        # Quote-delay is a live system flag; keep across day reset unless cleared by success.
        # position_cache is reloaded from DB as needed; keep across day.

    def set_signal(
        self,
        stock_code: str,
        signal_type: str,
        *,
        t1_note: bool = False,
        is_limit_up: bool = False,
    ) -> None:
        self.signal_state[stock_code] = signal_type
        self.signal_meta[stock_code] = {
            "t1_note": bool(t1_note),
            "is_limit_up": bool(is_limit_up),
        }


runtime_state = RuntimeState()
