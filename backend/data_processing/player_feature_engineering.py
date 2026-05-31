"""
Player-level feature engineering.

Aggregates per-player match stats from the DB to match-level squad features:
  - home/away_squad_xg_avg:     mean total xG of expected XI over last 5 starts per player
  - home/away_squad_xa_avg:     same for expected assists
  - home/away_availability_score: fraction of squad available (0–1), sourced from
                                  PlayerAvailability; defaults to 1.0 when no data.

Call build_player_features(match_df, db) before building match features.
Returns a copy of match_df with the six columns appended.
"""

import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import timedelta
from sqlalchemy.orm import Session

from app.models import PlayerMatchStats, PlayerAvailability, ConfirmedLineup

# Rolling windows
FORM_WINDOW = 5           # last N starts per player to estimate form xG
SQUAD_SIZE = 11           # expected XI size for normalisation
AVAILABILITY_WINDOW_DAYS = 3  # look forward this many days for availability records


def _load_confirmed_lineups(db: Session, competition: str | None = None) -> pd.DataFrame:
    """Load ConfirmedLineup rows — starters are comma-separated player names."""
    q = db.query(ConfirmedLineup)
    if competition:
        q = q.filter(ConfirmedLineup.competition == competition)
    rows = q.all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "team":       r.team,
        "match_date": r.match_date,
        "starters":   [s.strip() for s in (r.starters or "").split(",") if s.strip()],
    } for r in rows])


def _squad_xg_from_lineup(
    starters: list[str],
    before_date: pd.Timestamp,
    stats_df: pd.DataFrame,
    _team_cache: dict,
) -> tuple[float, float]:
    """Compute squad xG/xA using confirmed starting XI rather than rolling-all-starters."""
    if not starters or stats_df.empty:
        return 0.0, 0.0
    total_xg, total_xa, found = 0.0, 0.0, 0
    for name in starters:
        # Look up in stats_df by player_name (case-insensitive substring)
        name_lower = name.lower()
        player_rows = stats_df[
            stats_df["player_name"].str.lower().str.contains(name_lower, na=False)
        ]
        player_rows = player_rows[player_rows["match_date"] < before_date]
        if player_rows.empty:
            continue
        recent = player_rows.nlargest(5, "match_date")
        total_xg += recent["xg"].mean()
        total_xa += recent["xa"].mean()
        found += 1
    if found == 0:
        return 0.0, 0.0
    # Scale up to full XI (some players may not have stats)
    scale = len(starters) / max(found, 1)
    return round(total_xg * scale, 4), round(total_xa * scale, 4)


def _load_player_stats(db: Session, competition: str | None = None) -> pd.DataFrame:
    """Load PlayerMatchStats from DB into a DataFrame."""
    q = db.query(PlayerMatchStats)
    if competition:
        q = q.filter(PlayerMatchStats.competition == competition)
    rows = q.all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "player_name": r.player_name,
        "team":        r.team,
        "match_date":  r.match_date,
        "minutes":     r.minutes or 0,
        "xg":          r.xg or 0.0,
        "xa":          r.xa or 0.0,
    } for r in rows])


def _load_availability(db: Session, competition: str | None = None) -> pd.DataFrame:
    """Load PlayerAvailability from DB into a DataFrame."""
    q = db.query(PlayerAvailability)
    if competition:
        q = q.filter(PlayerAvailability.competition == competition)
    rows = q.all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "player_name": r.player_name,
        "team":        r.team,
        "match_date":  r.match_date,
        "status":      r.status,
    } for r in rows])


def _squad_xg_for_team(
    team: str,
    before_date: pd.Timestamp,
    stats_df: pd.DataFrame,
    window: int = FORM_WINDOW,
    _team_cache: dict | None = None,
) -> tuple[float, float]:
    """
    Returns (squad_xg_avg, squad_xa_avg). Pass `_team_cache` (a pre-grouped
    dict keyed by team) to avoid O(n) filtering on the full stats_df per call.
    """
    if _team_cache is not None:
        team_df = _team_cache.get(team, pd.DataFrame())
        if team_df.empty:
            return 0.0, 0.0
        team_df = team_df[
            (team_df["match_date"] < before_date) &
            (team_df["minutes"] > 45)
        ]
    else:
        team_df = stats_df[
            (stats_df["team"] == team) &
            (stats_df["match_date"] < before_date) &
            (stats_df["minutes"] > 45)
        ]
    if team_df.empty:
        return 0.0, 0.0

    # Per player: mean xG/xA over last `window` appearances
    player_avgs = (
        team_df
        .sort_values("match_date")
        .groupby("player_name")
        .tail(window)
        .groupby("player_name")
        .agg(avg_xg=("xg", "mean"), avg_xa=("xa", "mean"), total_minutes=("minutes", "sum"))
        .reset_index()
    )

    # Top SQUAD_SIZE players by total minutes = proxy for expected XI
    top_xi = player_avgs.nlargest(SQUAD_SIZE, "total_minutes")
    return float(top_xi["avg_xg"].sum()), float(top_xi["avg_xa"].sum())


def _availability_score(
    team: str,
    match_date: pd.Timestamp,
    avail_df: pd.DataFrame,
) -> float:
    """
    Fraction of players listed as Available (or not listed at all) for this team
    around this match date. Returns 1.0 if no availability data exists.
    """
    if avail_df.empty:
        return 1.0

    window_start = match_date - timedelta(days=1)
    window_end   = match_date + timedelta(days=AVAILABILITY_WINDOW_DAYS)

    team_avail = avail_df[
        (avail_df["team"] == team) &
        (avail_df["match_date"] >= window_start) &
        (avail_df["match_date"] <= window_end)
    ]
    if team_avail.empty:
        return 1.0

    n_total     = len(team_avail)
    n_available = (team_avail["status"] == "Available").sum()
    return float(n_available / n_total) if n_total > 0 else 1.0


def build_player_features(
    match_df: pd.DataFrame,
    db: Session,
    competition: str | None = None,
) -> pd.DataFrame:
    """
    Appends six player-level columns to match_df:
        home_squad_xg_avg, home_squad_xa_avg,
        away_squad_xg_avg, away_squad_xa_avg,
        home_availability_score, away_availability_score

    Falls back gracefully to 0.0 / 1.0 when player tables are empty.

    Args:
        match_df:    DataFrame with columns home_team, away_team, match_date
        db:          SQLAlchemy session
        competition: Optional filter — pass competition code to narrow DB queries
    """
    stats_df   = _load_player_stats(db, competition)
    avail_df   = _load_availability(db, competition)
    lineup_df  = _load_confirmed_lineups(db, competition)

    has_stats  = not stats_df.empty
    has_avail  = not avail_df.empty
    has_lineup = not lineup_df.empty

    # Build a lookup: (team, date_str) → list[starters]
    lineup_lut: dict[tuple[str, str], list[str]] = {}
    if has_lineup:
        lineup_df["match_date"] = pd.to_datetime(lineup_df["match_date"])
        for _, row in lineup_df.iterrows():
            key = (row["team"], str(row["match_date"])[:10])
            lineup_lut[key] = row["starters"]

    match_df = match_df.copy()

    # Fast path: when both tables are empty, skip iterrows entirely
    if not has_stats and not has_avail:
        match_df["home_squad_xg_avg"]       = 0.0
        match_df["home_squad_xa_avg"]       = 0.0
        match_df["away_squad_xg_avg"]       = 0.0
        match_df["away_squad_xa_avg"]       = 0.0
        match_df["home_availability_score"] = 1.0
        match_df["away_availability_score"] = 1.0
        return match_df

    match_df["match_date"] = pd.to_datetime(match_df["match_date"])

    # Pre-group player stats by team to avoid O(n) filtering per match row
    team_cache: dict = {}
    if has_stats:
        stats_df["match_date"] = pd.to_datetime(stats_df["match_date"])
        team_cache = {team: grp for team, grp in stats_df.groupby("team")}

    # Pre-fill defaults for all rows, then compute only for upcoming rows
    match_df["home_squad_xg_avg"]       = 0.0
    match_df["home_squad_xa_avg"]       = 0.0
    match_df["away_squad_xg_avg"]       = 0.0
    match_df["away_squad_xa_avg"]       = 0.0
    match_df["home_availability_score"] = 1.0
    match_df["away_availability_score"] = 1.0

    # Only compute player features for rows that need them (upcoming matches)
    upcoming_mask = match_df.get("status", pd.Series(dtype=str)).isin(
        ["SCHEDULED", "TIMED"]
    )
    rows_to_compute = match_df[upcoming_mask] if upcoming_mask.any() else match_df

    h_xg, h_xa, a_xg, a_xa = [], [], [], []
    h_avail, a_avail = [], []

    for _, row in rows_to_compute.iterrows():
        home = row["home_team"]
        away = row["away_team"]
        date = row["match_date"]

        if has_stats:
            date_str = str(date)[:10]
            # Use confirmed lineup starters when available (more accurate)
            h_starters = lineup_lut.get((home, date_str))
            a_starters = lineup_lut.get((away, date_str))

            if h_starters:
                hxg, hxa = _squad_xg_from_lineup(h_starters, date, stats_df, team_cache)
            else:
                hxg, hxa = _squad_xg_for_team(home, date, stats_df, _team_cache=team_cache)

            if a_starters:
                axg, axa = _squad_xg_from_lineup(a_starters, date, stats_df, team_cache)
            else:
                axg, axa = _squad_xg_for_team(away, date, stats_df, _team_cache=team_cache)
        else:
            hxg = hxa = axg = axa = 0.0

        h_xg.append(hxg);  h_xa.append(hxa)
        a_xg.append(axg);  a_xa.append(axa)

        if has_avail:
            h_avail.append(_availability_score(home, date, avail_df))
            a_avail.append(_availability_score(away, date, avail_df))
        else:
            h_avail.append(1.0)
            a_avail.append(1.0)

    match_df.loc[rows_to_compute.index, "home_squad_xg_avg"]       = h_xg
    match_df.loc[rows_to_compute.index, "home_squad_xa_avg"]       = h_xa
    match_df.loc[rows_to_compute.index, "away_squad_xg_avg"]       = a_xg
    match_df.loc[rows_to_compute.index, "away_squad_xa_avg"]       = a_xa
    match_df.loc[rows_to_compute.index, "home_availability_score"] = h_avail
    match_df.loc[rows_to_compute.index, "away_availability_score"] = a_avail

    return match_df
