"""Application settings, read from environment variables / .env file."""

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    CorsOriginsType = list[str]
    EncryptionKeysType = list[str]
else:
    CorsOriginsType = list[str] | str
    EncryptionKeysType = list[str] | str


class Settings(BaseSettings):
    """Runtime configuration for wealthdock-server.

    Values are sourced from environment variables (or a `.env` file in
    local development, see `.env.example` for the full list of keys).
    """

    cors_origins: CorsOriginsType = ["http://localhost:5173", "http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://wealthdock:wealthdock@localhost:5432/wealthdock"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    encryption_keys: EncryptionKeysType = Field(
        validation_alias=AliasChoices("encryption_keys", "encryption_key")
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Parse CORS origins from list, JSON array string, or comma-separated string."""
        if isinstance(v, str):
            v_stripped = v.strip()
            if v_stripped.startswith("[") and v_stripped.endswith("]"):
                try:
                    import json

                    parsed = json.loads(v_stripped)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [origin.strip() for origin in v_stripped.split(",") if origin.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        raise TypeError("cors_origins must be a list or a string")

    @field_validator("encryption_keys", mode="before")
    @classmethod
    def parse_encryption_keys(cls, v: Any) -> list[str]:
        """Parse encryption keys from list or comma-separated string."""
        if isinstance(v, str):
            # Support comma-separated keys or JSON array
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json

                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed]
                except Exception:
                    pass
            return [k.strip() for k in v.split(",") if k.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v]
        raise TypeError("encryption_keys must be a list or a string")

    @field_validator("encryption_keys")
    @classmethod
    def validate_encryption_keys(cls, v: list[str]) -> list[str]:
        """Ensure there is at least one key and all keys are valid Fernet keys."""
        if not v:
            raise ValueError("At least one encryption key must be provided.")

        from cryptography.fernet import Fernet

        for key in v:
            try:
                Fernet(key.encode("utf-8"))
            except Exception as e:
                raise ValueError(
                    f"Invalid encryption key: '{key}'. "
                    "It must be a 32-byte, url-safe, base64 key. "
                    "Generate one: python -c "
                    '"from cryptography.fernet import Fernet; '
                    'print(Fernet.generate_key().decode())"'
                ) from e
        return v


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance.

    Cached so repeated calls (e.g. via FastAPI dependency injection) don't
    re-parse the environment on every request.
    """
    return Settings()  # type: ignore[call-arg]
