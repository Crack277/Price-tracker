from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    proxy_url: str | None = Field(None, alias="PROXY_URL")

    # PostgreSQL
    db_host: str = Field("localhost", alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_user: str = Field("pricetracker", alias="DB_USER")
    db_password: SecretStr = Field("pricetracker_pass", alias="DB_PASSWORD")
    db_name: str = Field("pricetracker_db", alias="DB_NAME")

    # Redis
    redis_host: str = Field("localhost", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_db: int = Field(0, alias="REDIS_DB")

    # Parser
    parser_timeout_seconds: int = Field(30, alias="PARSER_TIMEOUT_SECONDS")
    # Отдельный прокси для парсинга (если понадобится). По умолчанию парсер
    # ходит напрямую: PROXY_URL — это прокси для Telegram, с него маркетплейсы
    # блокируют запросы.
    parser_proxy_url: str | None = Field(None, alias="PARSER_PROXY_URL")

    # Scheduler
    price_check_interval_minutes: int = Field(30, alias="PRICE_CHECK_INTERVAL_MINUTES")

    # Logging
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def database_url(self) -> str:
        password = self.db_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def sync_database_url(self) -> str:
        password = self.db_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def parser_proxy_url_or_none(self) -> str | None:
        if self.parser_proxy_url and self.parser_proxy_url.strip():
            return self.parser_proxy_url.strip()
        return None

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def proxy_url_or_none(self) -> str | None:
        if self.proxy_url and self.proxy_url.strip():
            return self.proxy_url.strip()
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()