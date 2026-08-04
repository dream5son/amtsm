from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    sqlite_path: str = "data/amtsm.db"

    # Schedule placeholders
    baseline_hour: int = 8
    baseline_minute: int = 30
    snapshot_hour: int = 15
    snapshot_minute: int = 30


settings = Settings()
