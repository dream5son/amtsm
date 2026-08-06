from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    sqlite_path: str = str((Path(__file__).resolve().parents[2] / "data" / "amtsm.db"))

    # Schedule placeholders
    baseline_hour: int = 8
    baseline_minute: int = 30
    snapshot_hour: int = 15
    snapshot_minute: int = 30
    polling_interval_seconds: int = 5
    polling_batch_size: int = 50
    polling_request_timeout_seconds: float = 3.0
    polling_request_retries: int = 1


settings = Settings()
