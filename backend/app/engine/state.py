class RuntimeState:
    """In-memory state holder for baseline cache and per-day alert frequency control."""

    def __init__(self) -> None:
        self.baseline_cache: dict[str, dict] = {}
        self.sent_signal_keys: set[tuple[str, str, str]] = set()
        self.job_status: str = "IDLE"  # IDLE | RUNNING | FAILED | SUCCESS

    def reset_daily(self) -> None:
        self.baseline_cache.clear()
        self.sent_signal_keys.clear()


runtime_state = RuntimeState()
