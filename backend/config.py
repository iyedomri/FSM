import base64
from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    environment: str = "production"
    public_origin: str = "https://synapse.example.edu"
    secure_cookies: bool = True
    database_url: str = Field(repr=False)
    redis_url: str = Field(repr=False)
    jwt_secret: SecretStr
    token_encryption_key: SecretStr
    timezone: str = "Africa/Tunis"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"
    pinecone_api_key: SecretStr = SecretStr("")
    pinecone_index_host: str = ""
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    registration_invite: SecretStr
    max_pdf_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_source_tokens: int = Field(default=18000, ge=1000, le=24000)

    @model_validator(mode="after")
    def validate_settings(self):
        origin = urlsplit(self.public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("PUBLIC_ORIGIN must be an HTTP(S) origin")
        if origin.path or origin.query or origin.fragment or origin.username:
            raise ValueError("PUBLIC_ORIGIN must not contain a path, credentials or query")
        if len(self.jwt_secret.get_secret_value()) < 64:
            raise ValueError("JWT_SECRET needs at least 64 random characters")
        try:
            key = base64.b64decode(self.token_encryption_key.get_secret_value(), validate=True)
        except ValueError as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be standard base64") from exc
        if len(key) != 32:
            raise ValueError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes")
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Use PostgreSQL with the asyncpg driver")
        if self.environment not in {"production", "development", "test"}:
            raise ValueError("Invalid ENVIRONMENT")
        if self.environment == "production" and (not self.secure_cookies or origin.scheme != "https"):
            raise ValueError("Production requires HTTPS and SECURE_COOKIES=true")
        if len(self.registration_invite.get_secret_value()) < 24:
            raise ValueError("REGISTRATION_INVITE must be at least 24 random characters")
        ZoneInfo(self.timezone)
        return self

    @property
    def refresh_cookie(self):
        return "__Host-synapse-refresh" if self.secure_cookies else "synapse-refresh-dev"

    @property
    def oauth_state_cookie(self):
        return "__Host-synapse-oauth-state" if self.secure_cookies else "synapse-oauth-state-dev"

    @property
    def google_redirect_uri(self):
        return self.public_origin + "/api/integrations/google/callback"


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
