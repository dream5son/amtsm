class RuntimeState:
    """In-memory state holder for baseline cache and per-day alert frequency control."""

    def __init__(self) -> None:
        self.baseline_cache: dict[str, dict] = {}
        self.signal_state: dict[str, str] = {}
        self.signal_trade_date: str | None = None
        self.sent_signal_keys: set[tuple[str, str, str]] = set()
        self.job_status: str = "IDLE"  # IDLE | RUNNING | FAILED | SUCCESS

    def reset_daily(self) -> None:
        self.baseline_cache.clear()
        self.signal_state.clear()
        self.signal_trade_date = None
        self.sent_signal_keys.clear()


runtime_state = RuntimeState()
