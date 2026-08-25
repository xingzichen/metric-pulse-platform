"""应用配置定义与环境变量加载。

所有设置使用 ``MP_`` 前缀，并可由项目根目录的 ``.env`` 覆盖。模型串行、超时、来源缓存和
浏览器降级等参数集中在这里，避免在采集器内部散落难以审计的运行常量。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理进程级配置，并把部署约束固化为可校验的类型。

    字段名会由 pydantic-settings 自动映射为 ``MP_<字段名>`` 环境变量。这里特意不为
    模型并发提供可调参数：本项目绑定单实例本地模型，串行约束由跨进程文件锁保证。
    """

    model_config = SettingsConfigDict(env_prefix="MP_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./var/metric-pulse.db"
    object_root: Path = Path("./var/objects")
    export_root: Path = Path("./var/exports")
    source_cache_root: Path = Path("./var/source-cache")
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
    omlx_timeout_seconds: float = 900
    omlx_max_output_tokens: int = 4096
    sheet_analysis_max_output_tokens: int = 2048
    synthesize_max_output_tokens: int = 4096
    verify_max_output_tokens: int = 4096
    vision_table_max_output_tokens: int = 8192
    vision_table_retry_max_output_tokens: int = 16_384
    omlx_lock_path: Path = Path("./var/omlx-single-channel.lock")
    unit_lease_seconds: int = 1800
    retry_base_seconds: int = 60
    vision_analysis_enabled: bool = True
    vision_table_enrichment_enabled: bool = True
    search_url: str = ""
    search_timeout_seconds: float = 60
    search_min_interval_seconds: float = 60
    search_retry_delay_seconds: float = 60
    github_api_token: str = ""
    source_fetch_concurrency: int = 3
    source_cache_ttl_seconds: int = 86_400
    source_transient_cooldown_base_seconds: float = 60
    source_challenge_cooldown_seconds: float = 3_600
    source_cooldown_max_seconds: float = 3_600
    source_host_min_interval_seconds: float = 2
    browser_fallback_enabled: bool = True
    browser_timeout_seconds: float = 180
    browser_settle_seconds: float = 5
    browser_min_content_chars: int = 500
    browser_site_cooldown_seconds: float = 30
    ssrf_proxy_networks: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_string_lists(cls, value: object) -> object:
        """允许在环境变量中用逗号分隔多个 CORS 来源。"""

        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("omlx_model")
    @classmethod
    def require_fixed_local_model(cls, value: str) -> str:
        """拒绝意外切换模型，确保生产提示词和验收结果具有一致语义。"""

        if value != "Qwen3.8-27B-6bit":
            raise ValueError("Metric Pulse requires the fixed local model Qwen3.8-27B-6bit")
        return value

    def ensure_directories(self) -> None:
        """在应用初始化时创建所有本地持久化目录。"""

        self.object_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.source_cache_root.mkdir(parents=True, exist_ok=True)
        self.omlx_lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True,
                exist_ok=True,
            )


@lru_cache
def get_settings() -> Settings:
    """返回进程内单例配置，并只在首次读取时准备目录。"""

    settings = Settings()
    settings.ensure_directories()
    return settings
