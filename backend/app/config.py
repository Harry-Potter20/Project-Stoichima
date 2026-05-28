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

    # Leagues
    premier_league_id: str = "PL"
    la_liga_id: str = "PD"
    bundesliga_id: str = "BL1"
    serie_a_id: str = "SA"
    ligue_1_id: str = "FL1"

    # Rate limiting: football-data.org free tier = 10 req/min
    api_rate_limit_delay: float = 6.0  # seconds between requests

    # MLflow — defaults to local SQLite (file-based store deprecated Feb 2026)
    # For DagHub: https://dagshub.com/<username>/<repo>.mlflow
    mlflow_tracking_uri: str = "sqlite:///mlflow.db"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance — the @lru_cache means this only
    reads the .env file once, not on every request.
    """
    return Settings()
