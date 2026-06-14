-- Migration 003: sport field, referee, opening odds, tennis tables
-- Run from backend/ with: sqlite3 football.db < migrations/003_sport_referee_tennis.sql

-- Matches: sport, referee, opening odds
ALTER TABLE matches ADD COLUMN sport TEXT NOT NULL DEFAULT 'football';
ALTER TABLE matches ADD COLUMN referee TEXT;
ALTER TABLE matches ADD COLUMN opening_home_odds REAL;
ALTER TABLE matches ADD COLUMN opening_draw_odds REAL;
ALTER TABLE matches ADD COLUMN opening_away_odds REAL;

-- Predictions: sport
ALTER TABLE predictions ADD COLUMN sport TEXT NOT NULL DEFAULT 'football';

-- Bet log: sport
ALTER TABLE bet_log ADD COLUMN sport TEXT NOT NULL DEFAULT 'football';

-- Tennis matches
CREATE TABLE IF NOT EXISTS tennis_matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tourney_date    DATETIME NOT NULL,
    tourney_name    TEXT NOT NULL,
    tour            TEXT NOT NULL,
    surface         TEXT,
    round           TEXT,
    tourney_level   TEXT,
    player1_name    TEXT NOT NULL,
    player2_name    TEXT NOT NULL,
    player1_rank    INTEGER,
    player2_rank    INTEGER,
    player1_seed    INTEGER,
    player2_seed    INTEGER,
    winner          INTEGER,
    p1_ace          INTEGER,
    p1_df           INTEGER,
    p1_svpt         INTEGER,
    p1_1st_in       INTEGER,
    p1_1st_won      INTEGER,
    p1_2nd_won      INTEGER,
    p1_bp_saved     INTEGER,
    p1_bp_faced     INTEGER,
    p2_ace          INTEGER,
    p2_df           INTEGER,
    p2_svpt         INTEGER,
    p2_1st_in       INTEGER,
    p2_1st_won      INTEGER,
    p2_2nd_won      INTEGER,
    p2_bp_saved     INTEGER,
    p2_bp_faced     INTEGER,
    score           TEXT,
    minutes         INTEGER,
    sackmann_id     TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_tennis_date ON tennis_matches(tourney_date);
CREATE INDEX IF NOT EXISTS idx_tennis_sackmann ON tennis_matches(sackmann_id);

-- Tennis player ELO
CREATE TABLE IF NOT EXISTS tennis_player_elo (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name     TEXT NOT NULL UNIQUE,
    tour            TEXT NOT NULL DEFAULT 'ATP',
    elo_overall     REAL NOT NULL DEFAULT 1500.0,
    elo_clay        REAL NOT NULL DEFAULT 1500.0,
    elo_grass       REAL NOT NULL DEFAULT 1500.0,
    elo_hard        REAL NOT NULL DEFAULT 1500.0,
    elo_carpet      REAL NOT NULL DEFAULT 1500.0,
    matches_played  INTEGER NOT NULL DEFAULT 0,
    as_of_date      DATETIME
);
CREATE INDEX IF NOT EXISTS idx_tennis_player ON tennis_player_elo(player_name);
