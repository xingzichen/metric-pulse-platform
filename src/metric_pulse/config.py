from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MP_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./var/metric-pulse.db"
    object_root: Path = Path("./var/objects")
    export_root: Path = Path("./var/exports")
    storage_backend: str = "filesystem"
    s3_endpoint_url: str = ""
    s3_bucket: str = "metric-pulse"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "auto"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_cookie_name: str = "metric_pulse_session"
    session_cookie_secure: bool = False
    session_ttl_hours: int = 24
    bootstrap_username: str = "admin"
    bootstrap_password: str = "change-me"
    max_upload_mb: int = 50

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    eager_tasks: bool = True

    omlx_base_url: str = "http://10.0.0.203:5008/v1"
    omlx_model: str = "Qwen3.8-27B-6bit"
    omlx_api_key: str = ""
    omlx_timeout_seconds: float = 180
    omlx_max_output_tokens: int = 2048
    omlx_max_concurrency: int = 1
    vision_analysis_enabled: bool = True
    collector_mode: str = "omlx"
    gold_workbook_path: Path | None = None
    search_url: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def ensure_directories(self) -> None:
        self.object_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True,
                exist_ok=True,
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
