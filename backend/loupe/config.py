from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Secrets are SecretStr so they never repr into logs."""

    model_config = SettingsConfigDict(env_prefix="LOUPE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./loupe.db"

    # GitHub
    github_token: SecretStr | None = None
    github_graphql_url: str = "https://api.github.com/graphql"
    github_timeout_seconds: float = 30.0

    # Sync
    backfill_days: int = Field(default=180, ge=7, le=730)
    sync_interval_minutes: int = Field(default=15, ge=1)
    background_sync_enabled: bool = True

    # LLM
    llm_provider: Literal["anthropic", "bedrock", "mock"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    bedrock_model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    aws_region: str = "us-east-1"
    llm_max_tokens: int = 1200

    # API
    max_window_days: int = Field(default=366, ge=1)
    cors_origins: list[str] = ["http://localhost:5173"]
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
