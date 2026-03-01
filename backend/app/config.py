from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # API
    football_data_api_key: str

    # Database
    database_url: str = "sqlite:///./football.db"

    # App
    environment: str = "development"
    debug: bool = True

    # Leagues we support in v1
    premier_league_id: str = "PL"
    la_liga_id: str = "PD"

    # Rate limiting: football-data.org free tier = 10 req/min
    api_rate_limit_delay: float = 6.0  # seconds between requests

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance — the @lru_cache means this only
    reads the .env file once, not on every request.
    """
    return Settings()
