from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_id_list(raw: str) -> set[int]:
    return {int(part) for part in raw.replace(";", ",").split(",") if part.strip()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = "placeholder"
    webhook_url: str = ""
    # Sent by Telegram in X-Telegram-Bot-Api-Secret-Token; forged webhook calls are rejected.
    webhook_secret: str = ""
    # Self-hosted Bot API server; also how the end-to-end test runs against a fake one.
    telegram_api_base: str = ""

    # Access control. Both lists are fail-closed: an empty ALLOWED_CHAT_IDS means
    # the bot ignores every chat, an empty ADMIN_USER_IDS means nobody can change settings.
    allowed_chat_ids: str = ""
    admin_user_ids: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tg_task_bot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    llm_provider: str = "mock"
    openai_api_key: str = ""
    openai_model: str = "openai/gpt-oss-120b"
    openai_base_url: str = ""
    # Chat content leaves this machine only when the data owner sets this to true.
    # Local endpoints (Ollama) are not affected.
    llm_external_allowed: bool = False
    llm_timeout_seconds: int = 60
    llm_max_output_tokens: int = 2500
    # Sent as `reasoning_effort` when set. Local "thinking" models such as gemma4 spend
    # their whole output budget on reasoning unless this is "none" (5x slower and
    # truncated answers otherwise). Cloud models differ in which values they accept.
    llm_reasoning_effort: str = ""

    # App
    app_env: str = "local"
    default_timezone: str = "Asia/Almaty"
    default_digest_time: str = "18:00"

    # Batched analysis. The defaults fit a free tier of ~8000 tokens per minute:
    # one batch per chat per tick, with room left for the model's answer.
    analysis_interval_seconds: int = 60
    analysis_batch_max_messages: int = 30
    analysis_batch_max_chars: int = 4000
    # A batch starts once this many messages are waiting, or the oldest has waited this long.
    analysis_min_batch_messages: int = 10
    analysis_max_wait_seconds: int = 300
    # Unparseable model answers shrink the batch; after this many the messages are set aside.
    analysis_max_attempts: int = 5
    # How many batches a digest may run to catch up before it is built.
    digest_flush_batches: int = 5

    # Clarifications
    clarifications_enabled: bool = True
    clarification_cooldown_hours: int = 4

    # A digest missed by a restart is still sent within this window.
    digest_catchup_hours: int = 6

    # Retention, in days. 0 keeps data forever.
    message_retention_days: int = 0
    raw_update_retention_days: int = 0

    # Database engine
    sql_echo: bool = False

    # Web dashboard (interactive task list opened from the bot)
    dashboard_secret: str = ""
    public_base_url: str = ""

    # Settings panel (python -m app.admin_panel). It refuses to start without a password,
    # and stays on loopback: reach it through an SSH tunnel, never by opening the port.
    admin_panel_password: str = ""
    panel_host: str = "127.0.0.1"
    panel_port: int = 8090
    # The panel creates this file; the bot sees it and exits so its supervisor restarts it.
    restart_flag_path: str = "restart.flag"
    # Groups the bot was added to but may not work in: ids and titles, for the panel.
    seen_chats_path: str = "seen_chats.json"
    # Stacks added in the panel. Plain text and may hold API keys, like .env.
    stacks_path: str = "stacks.json"
    # Where the built-in local stacks look for Ollama; in Docker it is another container.
    local_llm_url: str = "http://localhost:11434/v1"

    # Set to false when running `app.main` only as a dashboard sidecar next to
    # `app.polling` — otherwise both processes' schedulers fire and digests get
    # sent twice.
    enable_scheduler: bool = True

    @property
    def allowed_chats(self) -> set[int]:
        return _parse_id_list(self.allowed_chat_ids)

    @property
    def admins(self) -> set[int]:
        return _parse_id_list(self.admin_user_ids)


settings = Settings()


def reload_settings() -> None:
    """Re-read .env into the shared object, which every module holds a reference to."""
    fresh = Settings()
    for name in Settings.model_fields:
        setattr(settings, name, getattr(fresh, name))
