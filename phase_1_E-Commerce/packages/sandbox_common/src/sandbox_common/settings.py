#This is a place where the sandbox system reads all its settings converts it to a correct type and validates it. and makes it available to rest of the code


from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_DATABASE_SCHEMES = {"postgresql", "postgres"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str
    log_level: str = "INFO"
    database_url: str | None = None
    db_pool_min: int = 5
    db_pool_max: int = 20
    db_acquire_timeout_ms: int = 1000

    @field_validator("service_name")
    @classmethod
    def strip_service_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("service_name must not be empty")
        return stripped

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")
        return level

    @field_validator("database_url")
    @classmethod
    def check_database_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        scheme = urlparse(value).scheme
        if scheme not in _DATABASE_SCHEMES:
            raise ValueError("database_url scheme must be postgresql or postgres")
        return value

    @field_validator("db_pool_min", "db_acquire_timeout_ms")
    @classmethod
    def require_at_least_one(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @model_validator(mode="after")
    def pool_max_covers_min(self) -> "Settings":
        if self.db_pool_max < self.db_pool_min:
            raise ValueError("db_pool_max must be >= db_pool_min")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
