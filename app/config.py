import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:
    from pydantic import BaseModel as BaseSettings

    class SettingsConfigDict(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_debug: bool = False
    app_base_url: str = "http://localhost:8000"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_admin_ids: str = ""
    webhook_mode: bool = False

    database_url: str = "sqlite+aiosqlite:///./receiptbot.db"
    redis_url: str = "redis://localhost:6379/0"
    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/2"

    storage_backend: str = "local"
    local_storage_path: Path = Path("./storage")
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "receipts"
    s3_region: str = "auto"

    google_vision_api_key: str = ""
    google_application_credentials: str = ""
    google_service_account_json: str = ""
    exchangerate_api_key: str = ""
    openai_api_key: str = ""
    api_secret_key: str = ""
    ocr_engine: str = "mock"
    ocr_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    default_language: str = "ru"
    default_currency: str = "UAH"

    @property
    def admin_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.telegram_admin_ids.split(",") if value.strip()}

    def __init__(self, **data):
        env_defaults = {}
        for field_name in self.__class__.model_fields:
            env_key = field_name.upper()
            if env_key in os.environ:
                env_defaults[field_name] = os.environ[env_key]
        env_defaults.update(data)
        super().__init__(**env_defaults)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
