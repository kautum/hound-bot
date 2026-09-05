"""Application settings, loaded from environment variables.

No secret ever gets a hardcoded default. Fields required for a given code path
are validated where they're used (e.g. security.py raises if ENCRYPTION_KEY is
missing when encryption is actually attempted), not eagerly at import time —
that would break `pytest` for anyone who hasn't configured Slack/Google/Groq
yet but wants to run the unit tests that don't need them.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/slack_workplace_assistant"

    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_signing_secret: str | None = None

    google_client_id: str | None = None
    google_client_secret: str | None = None

    groq_api_key: str | None = None

    encryption_key: str | None = None
    encryption_key_version: int = 1

    cron_shared_secret: str | None = None


settings = Settings()
