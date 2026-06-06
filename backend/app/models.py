from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, JSON, Index
from sqlalchemy.sql import func
from app.database import Base

class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    match_date = Column(DateTime, nullable=False)
    matchday = Column(Integer, nullable=True)
    home_team_id = Column(Integer, nullable=True)
    away_team_id = Column(Integer, nullable=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    home_team_score = Column(Integer, nullable=True)
    away_team_score = Column(Integer, nullable=True)
    home_team_xG = Column(Float, nullable=True)
    away_team_xG = Column(Float, nullable=True)
    competition = Column(String, nullable=False)
    season = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    result = Column(String, nullable=True)
    total_goals = Column(Integer, nullable=True)
    over_1_5_goals = Column(Boolean, nullable=True)
    over_2_5_goals = Column(Boolean, nullable=True)
    btts = Column(Boolean, nullable=True)
    home_team_shots = Column(Integer, nullable=True)
    away_team_shots = Column(Integer, nullable=True)
    home_team_shots_on_target = Column(Integer, nullable=True)
    away_team_shots_on_target = Column(Integer, nullable=True)
    home_team_corners = Column(Integer, nullable=True)
    away_team_corners = Column(Integer, nullable=True)
    home_team_yellow_cards = Column(Integer, nullable=True)
    away_team_yellow_cards = Column(Integer, nullable=True)
    home_team_red_cards = Column(Integer, nullable=True)
    away_team_red_cards = Column(Integer, nullable=True)
    home_team_fouls = Column(Integer, nullable=True)
    away_team_fouls = Column(Integer, nullable=True)
    # International / tournament fields
    is_neutral_venue = Column(Boolean, nullable=True, default=False)
    tournament_stage = Column(String, nullable=True)
    tournament_group = Column(String, nullable=True)

    # Historical bookmaker odds (from football-data.co.uk CSVs)
    b365_home  = Column(Float, nullable=True)   # Bet365 closing odds
    b365_draw  = Column(Float, nullable=True)
    b365_away  = Column(Float, nullable=True)
    ps_home    = Column(Float, nullable=True)   # Pinnacle closing odds
    ps_draw    = Column(Float, nullable=True)
    ps_away    = Column(Float, nullable=True)
    max_home   = Column(Float, nullable=True)   # Market max odds
    max_draw   = Column(Float, nullable=True)
    max_away   = Column(Float, nullable=True)
    avg_home   = Column(Float, nullable=True)   # Market average odds
    avg_draw   = Column(Float, nullable=True)
    avg_away   = Column(Float, nullable=True)

    # API-Football fixture ID (for lineup lookups)
    api_football_id = Column(Integer, nullable=True, index=True)

    # Sport (default 'football'; future: 'tennis', 'basketball')
    sport = Column(String, nullable=False, default="football", server_default="football")

    # Referee — populated from football-data.co.uk CSV imports
    referee = Column(String, nullable=True)

    # Opening bookmaker odds (first available, before sharp money moves lines)
    # Stored when first import runs; closing odds stay in b365_* / ps_* columns
    opening_home_odds = Column(Float, nullable=True)
    opening_draw_odds = Column(Float, nullable=True)
    opening_away_odds = Column(Float, nullable=True)

    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team} ({self.match_date.date()})>"

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    country = Column(String, nullable=False)

    def __repr__(self):
        return f"<Team {self.name} ({self.country})>"


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    competition = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    match_date = Column(DateTime, nullable=False)

    # Outcome
    predicted_outcome = Column(String, nullable=False)     # H / D / A
    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)

    # Over/Under 2.5
    predicted_over_2_5 = Column(Boolean, nullable=False)
    over_2_5_prob = Column(Float, nullable=False)

    # BTTS
    predicted_btts = Column(Boolean, nullable=False)
    btts_prob = Column(Float, nullable=False)

    # Actuals (filled in once the match is finished)
    actual_outcome = Column(String, nullable=True)
    actual_over_2_5 = Column(Boolean, nullable=True)
    actual_btts = Column(Boolean, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    model_version = Column(String, nullable=True)   # e.g. "1.2.0" — set at prediction time

    sport = Column(String, nullable=False, default="football", server_default="football")

    def __repr__(self):
        return f"<Prediction {self.home_team} vs {self.away_team} ({self.match_date.date()})>"


class Shot(Base):
    __tablename__ = "shots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    understat_id = Column(String, nullable=True, unique=True)   # Understat's own shot id for upserts
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    competition = Column(String, nullable=False)
    season = Column(Integer, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    minute = Column(Integer, nullable=True)
    player = Column(String, nullable=True)
    player_team = Column(String, nullable=True)
    x = Column(Float, nullable=False)           # Understat pitch coords (0–1 normalised)
    y = Column(Float, nullable=False)
    result = Column(String, nullable=True)       # Goal/SavedShot/MissedShots/BlockedShot
    shot_type = Column(String, nullable=True)    # LeftFoot/RightFoot/Head
    situation = Column(String, nullable=True)    # OpenPlay/SetPiece/FromCorner/DirectFreekick/Penalty
    last_action = Column(String, nullable=True)
    understat_xg = Column(Float, nullable=True)  # Understat's own xG — baseline comparison

    def __repr__(self):
        return f"<Shot {self.player} {self.result} ({self.x:.2f},{self.y:.2f})>"


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    team = Column(String, nullable=False)
    competition = Column(String, nullable=False)

    def __repr__(self):
        return f"<Player {self.name} ({self.team})>"


class PlayerMatchStats(Base):
    __tablename__ = "player_match_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    # Denormalised for easy querying without joins
    player_name = Column(String, nullable=False)
    team = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    match_date = Column(DateTime, nullable=False)
    season = Column(Integer, nullable=False)
    minutes = Column(Integer, nullable=True)
    goals = Column(Integer, nullable=True, default=0)
    assists = Column(Integer, nullable=True, default=0)
    shots = Column(Integer, nullable=True, default=0)
    key_passes = Column(Integer, nullable=True, default=0)
    xg = Column(Float, nullable=True)        # own model xG (populated after xg_model is trained)
    xa = Column(Float, nullable=True)        # Understat's xA (expected assists)
    yellow_card = Column(Boolean, nullable=True, default=False)
    red_card = Column(Boolean, nullable=True, default=False)
    # Understat source id for upserts
    understat_player_id = Column(String, nullable=True)
    understat_match_id = Column(String, nullable=True)

    def __repr__(self):
        return f"<PlayerMatchStats {self.player_name} vs {self.match_date.date()}>"


class OddsSnapshot(Base):
    """
    Latest bookmaker odds for an upcoming match, fetched from The Odds API.
    One row per match — updated each time the odds ETL runs.
    """
    __tablename__ = "odds_snapshots"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    match_id        = Column(Integer, ForeignKey("matches.id"), nullable=True, index=True)
    competition     = Column(String, nullable=False)
    home_team       = Column(String, nullable=False)
    away_team       = Column(String, nullable=False)
    match_date      = Column(DateTime, nullable=False)

    # Best available decimal odds across bookmakers
    home_odds       = Column(Float, nullable=True)   # e.g. 2.10
    draw_odds       = Column(Float, nullable=True)
    away_odds       = Column(Float, nullable=True)

    # Implied probabilities (1 / decimal_odds, renormalised for vig removal)
    home_implied    = Column(Float, nullable=True)
    draw_implied    = Column(Float, nullable=True)
    away_implied    = Column(Float, nullable=True)

    # Per-outcome best bookmaker (true best-price shopping)
    bk_home         = Column(String, nullable=True)
    bk_draw         = Column(String, nullable=True)
    bk_away         = Column(String, nullable=True)

    # Arbitrage signal: > 0 means guaranteed profit exists across these books
    arb_margin      = Column(Float, nullable=True)

    bookmaker       = Column(String, nullable=True)   # primary bookmaker (best H odds)
    fetched_at      = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<OddsSnapshot {self.home_team} vs {self.away_team}>"


class EloRating(Base):
    """
    Stores the most-recent Elo rating per team.
    For club teams: sourced from clubelo.com.
    For international teams: computed from our own match history.
    """
    __tablename__ = "elo_ratings"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    team        = Column(String, nullable=False, index=True)
    competition = Column(String, nullable=True)   # None = international
    elo         = Column(Float, nullable=False)
    as_of_date  = Column(DateTime, nullable=False)
    source      = Column(String, nullable=True)   # clubelo / computed

    def __repr__(self):
        return f"<EloRating {self.team} {self.elo:.0f} ({self.as_of_date.date()})>"


class PlayerAvailability(Base):
    __tablename__ = "player_availability"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player_name = Column(String, nullable=False)
    team = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    match_date = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)          # Available / Injured / Suspended / Doubtful
    return_date = Column(DateTime, nullable=True)
    source = Column(String, nullable=True)           # api_football / manual

    def __repr__(self):
        return f"<PlayerAvailability {self.player_name} {self.status} ({self.match_date.date()})>"


class BetLog(Base):
    """
    Simulated bet record created whenever the model detects an edge >= threshold.
    Stake is sized using fractional Kelly criterion.
    Resolved once the match finishes.
    """
    __tablename__ = "bet_log"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    match_id         = Column(Integer, ForeignKey("matches.id"), nullable=True, index=True)
    competition      = Column(String, nullable=False)
    home_team        = Column(String, nullable=False)
    away_team        = Column(String, nullable=False)
    match_date       = Column(DateTime, nullable=False)

    # Which outcome the model backed
    bet_on           = Column(String, nullable=False)   # H / D / A
    model_prob       = Column(Float, nullable=False)
    implied_prob     = Column(Float, nullable=False)
    edge_pct         = Column(Float, nullable=False)    # (model - implied) * 100
    decimal_odds     = Column(Float, nullable=False)
    kelly_pct        = Column(Float, nullable=False)    # fractional Kelly stake %
    bookmaker        = Column(String, nullable=True)

    # Resolution
    actual_result    = Column(String, nullable=True)    # H / D / A — filled when FINISHED
    won              = Column(Boolean, nullable=True)
    profit_loss_units = Column(Float, nullable=True)    # +/- in units (1 unit = 1% of bankroll)
    resolved_at      = Column(DateTime, nullable=True)

    # Closing line odds — captured at settlement time for CLV calculation
    closing_home_odds = Column(Float, nullable=True)
    closing_draw_odds = Column(Float, nullable=True)
    closing_away_odds = Column(Float, nullable=True)
    # CLV = (entry_odds / closing_odds - 1) * 100 — stored at settlement
    clv_pct           = Column(Float, nullable=True)

    created_at       = Column(DateTime, server_default=func.now())
    sport            = Column(String, nullable=False, default="football", server_default="football")

    # Manual bet support
    source           = Column(String, nullable=False, default="auto", server_default="auto")  # auto / manual
    notes            = Column(String, nullable=True)
    tags             = Column(String, nullable=True)   # comma-separated tags

    # Market type for non-1X2 bets (e.g. "over_2.5", "btts_yes", "dnb_home")
    market           = Column(String, nullable=True, default="1x2")
    stake_amount     = Column(Float, nullable=True)    # real-currency stake at time of logging

    def __repr__(self):
        return f"<BetLog {self.home_team} vs {self.away_team} → {self.bet_on} @{self.decimal_odds}>"


class ConfirmedLineup(Base):
    """Starting XI confirmed by API-Football (~60 min before kickoff)."""
    __tablename__ = "confirmed_lineups"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    match_id     = Column(Integer, ForeignKey("matches.id"), nullable=True, index=True)
    competition  = Column(String, nullable=False)
    team         = Column(String, nullable=False)
    match_date   = Column(DateTime, nullable=False)
    formation    = Column(String, nullable=True)

    # Comma-separated player names for the starting XI
    starters     = Column(String, nullable=True)
    # Average rating of starters from our PlayerMatchStats history
    avg_xg       = Column(Float, nullable=True)    # mean xG of starters over last 5 matches
    avg_xa       = Column(Float, nullable=True)
    confirmed_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<ConfirmedLineup {self.team} {self.match_date.date()} [{self.formation}]>"


class ManagerialChange(Base):
    """
    Tracks manager/head-coach changes per team. Used as a disruption feature:
    teams with a new manager within the last 30 days show altered form patterns.
    Populated manually or via a scraper — not auto-fetched.
    """
    __tablename__ = "managerial_changes"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    team         = Column(String, nullable=False, index=True)
    competition  = Column(String, nullable=False)
    change_date  = Column(DateTime, nullable=False)
    old_manager  = Column(String, nullable=True)
    new_manager  = Column(String, nullable=True)
    created_at   = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<ManagerialChange {self.team} {self.change_date.date()} → {self.new_manager}>"


class BasketballMatch(Base):
    """Historical and upcoming basketball matches (NBA, EuroLeague, etc.)."""
    __tablename__ = "basketball_matches"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    home_team    = Column(String, nullable=False)
    away_team    = Column(String, nullable=False)
    match_date   = Column(DateTime, nullable=False)
    competition  = Column(String, nullable=False)     # NBA, EuroLeague, etc.
    season       = Column(Integer, nullable=True)
    status       = Column(String, nullable=False, default="SCHEDULED")

    home_score   = Column(Integer, nullable=True)
    away_score   = Column(Integer, nullable=True)
    result       = Column(String, nullable=True)      # H / A (no draws in basketball)

    home_elo     = Column(Float, nullable=True)
    away_elo     = Column(Float, nullable=True)

    def __repr__(self):
        return f"<BasketballMatch {self.home_team} vs {self.away_team} {self.match_date.date()}>"


class BasketballTeamElo(Base):
    """Current ELO rating per basketball team."""
    __tablename__ = "basketball_elo"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    team         = Column(String, nullable=False, index=True)
    competition  = Column(String, nullable=False)
    elo          = Column(Float, nullable=False, default=1500.0)
    matches      = Column(Integer, nullable=False, default=0)
    updated_at   = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<BasketballTeamElo {self.team} {self.elo:.0f}>"


# ── Tennis ────────────────────────────────────────────────────────────────────

class TennisMatch(Base):
    """
    Historical ATP/WTA match from Jeff Sackmann's open dataset.
    Used to train ELO ratings and the tennis prediction model.
    """
    __tablename__ = "tennis_matches"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    tourney_date     = Column(DateTime, nullable=False, index=True)
    tourney_name     = Column(String, nullable=False)
    tour             = Column(String, nullable=False)          # ATP / WTA
    surface          = Column(String, nullable=True)           # Clay/Grass/Hard/Carpet
    round            = Column(String, nullable=True)           # R128/R64/R32/R16/QF/SF/F
    tourney_level    = Column(String, nullable=True)           # G(rand Slam)/M(asters)/A/D(avis)

    player1_name     = Column(String, nullable=False)
    player2_name     = Column(String, nullable=False)
    player1_rank     = Column(Integer, nullable=True)
    player2_rank     = Column(Integer, nullable=True)
    player1_seed     = Column(Integer, nullable=True)
    player2_seed     = Column(Integer, nullable=True)
    winner           = Column(Integer, nullable=True)          # 1 or 2

    # Match stats for player1 (winner or loser depending on who is p1)
    p1_ace           = Column(Integer, nullable=True)
    p1_df            = Column(Integer, nullable=True)
    p1_svpt          = Column(Integer, nullable=True)
    p1_1st_in        = Column(Integer, nullable=True)
    p1_1st_won       = Column(Integer, nullable=True)
    p1_2nd_won       = Column(Integer, nullable=True)
    p1_bp_saved      = Column(Integer, nullable=True)
    p1_bp_faced      = Column(Integer, nullable=True)
    p2_ace           = Column(Integer, nullable=True)
    p2_df            = Column(Integer, nullable=True)
    p2_svpt          = Column(Integer, nullable=True)
    p2_1st_in        = Column(Integer, nullable=True)
    p2_1st_won       = Column(Integer, nullable=True)
    p2_2nd_won       = Column(Integer, nullable=True)
    p2_bp_saved      = Column(Integer, nullable=True)
    p2_bp_faced      = Column(Integer, nullable=True)

    score            = Column(String, nullable=True)
    minutes          = Column(Integer, nullable=True)

    # Sackmann match_id for upserts
    sackmann_id      = Column(String, nullable=True, unique=True, index=True)

    def __repr__(self):
        return f"<TennisMatch {self.player1_name} v {self.player2_name} {self.tourney_name} {self.round}>"


class TennisPlayerElo(Base):
    """
    Surface-specific Elo ratings computed from the full Sackmann dataset.
    One row per player — updated each time the training pipeline runs.
    """
    __tablename__ = "tennis_player_elo"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    player_name   = Column(String, nullable=False, unique=True, index=True)
    tour          = Column(String, nullable=False, default="ATP")
    elo_overall   = Column(Float, nullable=False, default=1500.0)
    elo_clay      = Column(Float, nullable=False, default=1500.0)
    elo_grass     = Column(Float, nullable=False, default=1500.0)
    elo_hard      = Column(Float, nullable=False, default=1500.0)
    elo_carpet    = Column(Float, nullable=False, default=1500.0)
    matches_played = Column(Integer, nullable=False, default=0)
    as_of_date    = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<TennisPlayerElo {self.player_name} overall={self.elo_overall:.0f}>"
# ─── Live match tracking ─────────────────────────────────────────────────────

class LiveMatchState(Base):
    """
    Current snapshot of an in-play match. One row per match; updated in-place by
    the live ingest job. Pre-match xG/lambdas are frozen at kickoff so the live
    Poisson predictor can apply time-decay without re-running the pre-match
    ensemble on every poll.
    """
    __tablename__ = "live_match_state"

    match_id       = Column(Integer, ForeignKey("matches.id"), primary_key=True)
    competition    = Column(String, nullable=False, index=True)
    home_team      = Column(String, nullable=False)
    away_team      = Column(String, nullable=False)
    kickoff_at     = Column(DateTime, nullable=False)
    status         = Column(String, default="NOT_STARTED", index=True)
        # NOT_STARTED, IN_PLAY, HT, IN_PLAY_2H, FT, AET, PEN, SUSPENDED
    minute         = Column(Integer, default=0)
    home_score     = Column(Integer, default=0)
    away_score     = Column(Integer, default=0)
    home_xg_live   = Column(Float, default=0.0)   # cumulative in-play xG
    away_xg_live   = Column(Float, default=0.0)
    home_red_cards = Column(Integer, default=0)
    away_red_cards = Column(Integer, default=0)
    home_yellow_cards = Column(Integer, default=0)
    away_yellow_cards = Column(Integer, default=0)
    # Pre-match anchors — frozen at kickoff, used by live Poisson update
    prematch_lambda_home = Column(Float, nullable=True)
    prematch_lambda_away = Column(Float, nullable=True)
    last_event_at  = Column(DateTime, nullable=True)
    last_polled_at = Column(DateTime, default=func.now())


class LiveMatchEvent(Base):
    """
    Append-only log of in-play events (goals, cards, subs, VAR reviews).
    Used by the timeline UI + Telegram alerts.
    """
    __tablename__ = "live_match_events"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    match_id    = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    minute      = Column(Integer, nullable=False)
    type        = Column(String, nullable=False)
        # goal, own_goal, penalty_goal, penalty_missed, yellow, red, sub,
        # var_check, half_start, half_end
    team        = Column(String, nullable=True)
    player      = Column(String, nullable=True)
    detail      = Column(JSON, nullable=True)
    api_event_id = Column(String, nullable=True, index=True)  # for dedup
    created_at  = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_live_events_match_min", "match_id", "minute"),
    )


class LivePredictionSnapshot(Base):
    """
    One row per minute per match — captures the live model output trajectory.
    Used by the probability sparkline on the live tab and by the CLV alerter
    (compares model probs vs live market odds).
    """
    __tablename__ = "live_prediction_snapshots"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    match_id        = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    minute          = Column(Integer, nullable=False)
    home_win_prob   = Column(Float, nullable=False)
    draw_prob       = Column(Float, nullable=False)
    away_win_prob   = Column(Float, nullable=False)
    over_2_5_prob   = Column(Float, nullable=True)
    btts_prob       = Column(Float, nullable=True)
    expected_total_goals = Column(Float, nullable=True)
    # Live market odds at this minute (for CLV / edge tracking)
    book_home_odds  = Column(Float, nullable=True)
    book_draw_odds  = Column(Float, nullable=True)
    book_away_odds  = Column(Float, nullable=True)
    bookmaker       = Column(String, nullable=True)
    snapshot_at     = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_live_snap_match_min", "match_id", "minute"),
    )



class NationalTeamSquad(Base):
    """
    Maps national team players to their nation. Lets player_props_model
    resolve a national team to a list of players whose CLUB-level
    PlayerMatchStats can then be aggregated for scoring rates.

    Updated by data_collection/squad_fetcher.py (API-Football /players/squads)
    or by manual seeding (data_collection/squad_seed.py).
    """
    __tablename__ = "national_team_squads"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    nation       = Column(String, nullable=False, index=True)
    player_name  = Column(String, nullable=False, index=True)
    club_team    = Column(String, nullable=True)
    position     = Column(String, nullable=True)
    shirt_number = Column(Integer, nullable=True)
    is_captain   = Column(Boolean, default=False)
    source       = Column(String, default="api_football")
    confirmed_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_squad_nation_player", "nation", "player_name", unique=True),
    )
