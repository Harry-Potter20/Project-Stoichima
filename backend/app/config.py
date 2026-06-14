from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # API
    football_data_api_key: str

    # Database
    database_url: str = "sqlite:///./football.db"

    # App
    environment: str = "development"
    debug: bool = True

    # Top-flight leagues
    premier_league_id: str = "PL"
    la_liga_id: str = "PD"
    bundesliga_id: str = "BL1"
    serie_a_id: str = "SA"
    ligue_1_id: str = "FL1"

    # Second-tier leagues
    championship_id: str = "ELC"
    bundesliga_2_id: str = "BL2"
    la_liga_2_id: str = "PD2"
    serie_b_id: str = "SB"
    ligue_2_id: str = "FL2"
    scottish_prem_id: str = "SPL"

    # Third-tier UK
    efl_league_one_id: str = "EL1"
    efl_league_two_id: str = "EL2"

    # Other European top-flights (high-value for edge — less sharp money)
    eredivisie_id: str = "ERE"
    belgian_pro_id: str = "JPL"
    primeira_liga_id: str = "PPL"
    super_lig_id: str = "TSL"
    greek_sl_id: str = "GSL"

    LOWER_LEAGUES: list = ["ELC", "BL2", "PD2", "SB", "FL2", "SPL", "EL1", "EL2"]
    OTHER_EUROPEAN: list = ["ERE", "JPL", "PPL", "TSL", "GSL"]

    # International competitions
    world_cup_id: str = "WC"
    euros_id: str = "EC"
    nations_league_id: str = "UNL"
    copa_america_id: str = "CA"

    INTERNATIONAL_COMPETITIONS: list = ["WC", "EC", "UNL", "CA"]

    # Rate limiting: football-data.org free tier = 10 req/min
    api_rate_limit_delay: float = 6.0  # seconds between requests

    # MLflow — defaults to local SQLite (file-based store deprecated Feb 2026)
    # For DagHub: https://dagshub.com/<username>/<repo>.mlflow
    mlflow_tracking_uri: str = "sqlite:///mlflow.db"

    # API-Football (free tier: 100 req/day) — set in .env when ready
    # https://www.api-football.com/
    api_football_key: str = ""

    # The Odds API (free tier: 500 req/month) — set in .env when ready
    # https://the-odds-api.com/
    odds_api_key: str = ""

    # Telegram alerting — set bot token + chat_id in .env to enable bet alerts
    # Create a bot via @BotFather, get chat_id from @userinfobot
    telegram_bot_token: str = ""
    telegram_chat_id:   str = ""
    telegram_min_edge:  float = 8.0   # only alert when model edge >= this %

    # Betting bot risk controls
    max_daily_kelly_pct: float = 15.0  # max total Kelly stake exposure per day (% of bankroll)
    daily_loss_limit_pct: float = 10.0 # stop logging new bets if today's P&L < -this % of bankroll
    bankroll_size: float = 1000.0      # actual bankroll in base currency (for real stake calculations)
    clv_alert_threshold: float = 5.0   # alert when beating closing line by >= this % (CLV signal)
    model_version: str = "1.0.0"       # bumped each retrain cycle; tagged on every Prediction row

    # Webhook — POST to this URL when edge >= min_edge (Discord/Slack/n8n compatible)
    webhook_url: str = ""              # set in .env to enable
    webhook_min_edge: float = 8.0      # only fire webhook when edge >= this %

    # Daily email digest — SMTP settings
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    digest_email_to: str = ""          # recipient address for daily digest

    # ELO mean-reversion for international teams
    intl_elo_reversion_rate: float = 0.05   # fraction toward 1500 per inactive month

    # ── Prediction tuning ────────────────────────────────────────────────────
    # Market anchoring: share of the 1X2 probability taken from the bookmaker
    # closing line (0 = pure model, 1 = pure market). The closing line is very
    # well calibrated; the league outcome model currently has NEGATIVE skill on
    # a clean PL holdout (model Brier 0.82 vs market 0.59, optimal weight 1.0 —
    # see validation/holdout.py), so we anchor hard to the market while keeping
    # a small model tilt. NO-OP where odds are absent (e.g. international/WC),
    # so this does not touch the tournament predictions. Lower toward 0 once the
    # outcome model is retrained with closing-odds features and beats the line.
    market_blend_weight: float = 0.7
    # Only call a Draw when its probability clears this floor AND beats both
    # sides — stops low-value argmax draw picks that tank accuracy.
    draw_pick_floor: float = 0.40

    # Admin API protection — when set, /admin/* endpoints require this value
    # in the X-Admin-Key header. Leave blank to keep them open (local dev).
    admin_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        protected_namespaces=(),   # allow model_* field names
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance — the @lru_cache means this only
    reads the .env file once, not on every request.
    """
    return Settings()
