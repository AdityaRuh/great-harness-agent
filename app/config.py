from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/harness"
    database_url_sync: str = "postgresql://user:pass@localhost:5432/harness"

    # OpenAI Codex (ChatGPT OAuth) — sole LLM provider for chat + code agents
    openai_codex_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    openai_codex_account_id: str = ""
    openai_codex_access_token: str = ""
    openai_codex_refresh_token: str = ""
    openai_codex_expires_ms: int = 0
    openai_codex_model: str = "gpt-5.5"
    openai_codex_model_strong: str = ""  # blank → falls back to openai_codex_model
    openai_codex_thinking: str = "medium"
    openai_codex_instructions: str = ""  # blank → llm.py default applies

    google_credentials_path: str = "credentials.json"
    hr_email: str = ""
    clawvatar_url: str = "ws://localhost:8765"
    knowledgebase_path: str = "./knowledgebase"
    app_name: str = "Great Harness Agent"
    app_url: str = "http://localhost:8000"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
