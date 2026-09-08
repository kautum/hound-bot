"""Application settings, loaded from environment variables.

No secret ever gets a hardcoded default. Fields required for a given code path
are validated where they're used (e.g. security.py raises if ENCRYPTION_KEY is
missing when encryption is actually attempted), not eagerly at import time —
that would break `pytest` for anyone who hasn't configured Slack/Google/Groq
yet but wants to run the unit tests that don't need them.

`database_url` is the one exception to "no hardcoded default" that actually
matters: it used to default to a role/db that doesn't exist on every machine,
which fails as a confusing asyncpg connection error deep in a fixture instead
of a clear message at startup. Now a missing DATABASE_URL fails loudly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # The dev ngrok tunnel URL locally, the Render URL in prod. See
    # ARCHITECTURE.md — dev and prod are separate Slack apps with separate
    # redirect URIs built from this value.
    public_base_url: str | None = None

    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_signing_secret: str | None = None

    google_client_id: str | None = None
    google_client_secret: str | None = None

    groq_api_key: str | None = None

    encryption_key: str | None = None
    encryption_key_version: int = 1
    # S6: set both during a key rotation window so rows still encrypted
    # under the old key keep decrypting while everything new (and every
    # re-encrypt) uses encryption_key. Unset once the rotation is complete.
    encryption_key_old: str | None = None
    encryption_key_old_version: int | None = None

    cron_shared_secret: str | None = None


settings = Settings()
