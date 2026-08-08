class RuntimeState:
    """In-memory state holder for baseline cache and per-day alert frequency control."""

    def __init__(self) -> None:
        self.baseline_cache: dict[str, dict] = {}
        self.signal_state: dict[str, str] = {}
        self.signal_trade_date: str | None = None
        self.sent_signal_keys: set[tuple[str, str, str]] = set()
        self.job_status: str = "IDLE"  # IDLE | RUNNING | FAILED | SUCCESS
        # User story 12: realtime quote resilience
        self.consecutive_poll_failures: int = 0
        self.quote_delay: bool = False
        self.quote_delay_since: str | None = None
        self.last_internal_alert_key: str | None = None
        # V2 risk: position snapshot cache + partial-TP ladder progress
        self.position_cache: dict[str, dict] = {}
        self.partial_tp_ladder_idx: dict[str, int] = {}
        self.exit_fired_today: set[str] = set()

    def reset_daily(self) -> None:
        self.baseline_cache.clear()
        self.signal_state.clear()
        self.signal_trade_date = None
        self.sent_signal_keys.clear()
        self.partial_tp_ladder_idx.clear()
        self.exit_fired_today.clear()
        # Quote-delay is a live system flag; keep across day reset unless cleared by success.
        # position_cache is reloaded from DB as needed; keep across day.


runtime_state = RuntimeState()
