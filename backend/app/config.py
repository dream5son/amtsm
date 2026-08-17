from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    sqlite_path: str = str(Path(__file__).resolve().parents[2] / "data" / "amtsm.db")

    # Schedule placeholders
    baseline_hour: int = 8
    baseline_minute: int = 30
    snapshot_hour: int = 15
    snapshot_minute: int = 30
    snapshot_fetch_retries: int = 2
    snapshot_fetch_retry_backoff_seconds: float = 0.5
    polling_interval_seconds: int = 5
    intraday_snapshot_interval_seconds: int = 60
    polling_batch_size: int = 50
    polling_request_timeout_seconds: float = 3.0
    # Max retry attempts after the first failed realtime fetch (user story 12).
    polling_request_retries: int = 3
    polling_request_retry_backoff_seconds: float = 0.5
    # Consecutive failed poll rounds before marking quote delay.
    polling_consecutive_failure_threshold: int = 5

    # Text notification channels (wechat, email; comma-separated)
    notify_channels: str = "wechat"

    # WeChat enterprise notifier (user story 07)
    wechat_corp_id: str = ""
    wechat_agent_id: str = ""
    wechat_secret: str = ""
    wechat_to_user: str = ""
    wechat_request_timeout_seconds: float = 10.0
    wechat_self_check_on_startup: bool = True

    # SMTP email notifier
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    # Alias accepted from .env ``SMTP_USER`` (common mailbox naming).
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = 10.0
    smtp_subject: str = "AMTSM 通知"
    smtp_self_check_on_startup: bool = True

    # Buy/sell alert delivery (user story 08+)
    alert_send_max_retries: int = 3
    alert_send_retry_backoff_seconds: float = 0.5

    # Backtest worker (V2 user stories 08-10)
    backtest_worker_interval_seconds: int = 3
    backtest_min_trade_count: int = 5
    backtest_snapshot_fetch_retries: int = 2
    backtest_snapshot_fetch_retry_backoff_seconds: float = 0.5


settings = Settings()
