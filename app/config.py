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
    anthropic_auth_token: str = ""
    anthropic_api_key: str = ""
    llm_model_fast: str = "claude-sonnet-4-20250514"
    llm_model_strong: str = "claude-opus-4-20250514"
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
