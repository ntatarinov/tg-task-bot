from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = "placeholder"
    webhook_url: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tg_task_bot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    llm_provider: str = "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""

    # App
    app_env: str = "local"
    default_timezone: str = "Asia/Almaty"
    default_digest_time: str = "18:00"

    # Analysis
    context_messages_limit: int = 20
    clarification_cooldown_hours: int = 4
    clarification_min_confidence: float = 0.7
    pending_retry_interval_seconds: int = 300

    # Database engine
    sql_echo: bool = False


settings = Settings()
