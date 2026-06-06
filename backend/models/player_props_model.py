"""
Player props model — anytime scorer, multi-goal, and first-scorer probabilities.

Approach (Poisson per-player):
  λ_player_per_90 = sum(xG in last N matches) / sum(minutes in last N matches) * 90
  λ_player_match  = λ_player_per_90 * (expected_minutes / 90) * opponent_defense_adj
  P(scores any)   = 1 - exp(-λ_player)
  P(2+)           = 1 - exp(-λ_player) - λ_player * exp(-λ_player)
  P(first scorer) ≈ (λ_player / λ_total_match) * P(at least one goal in match)

Opponent adjustment uses the DC model's defensive parameter — if the opponent
has a strong defense (defense_a more negative), reduce the player's λ.

This is intentionally simple. A full player-level model would include
positional priors, set-piece taker flags, and confirmed-lineup data — added
in follow-up iterations.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.models import PlayerMatchStats, NationalTeamSquad


PROP_LOOKBACK_DAYS  = 730    # 2-year window — accommodates gaps in club season data
PROP_MIN_MINUTES    = 270    # need at least 3 full matches' worth of minutes
DEFAULT_MINUTES     = 75     # assume starter; subs handled when lineup is confirmed


def player_per_90_xg(
    db: Session,
    player_name: str,
    competition_hint: Optional[str] = None,
    lookback_days: int = PROP_LOOKBACK_DAYS,
) -> Optional[float]:
    """
    Returns player's xG per 90 minutes over the lookback window, or None when
    sample is too thin.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(
            func.sum(PlayerMatchStats.xg).label("total_xg"),
            func.sum(PlayerMatchStats.minutes).label("total_min"),
        )
        .filter(
            PlayerMatchStats.player_name == player_name,
            PlayerMatchStats.match_date >= cutoff,
            PlayerMatchStats.minutes.isnot(None),
        )
        .one()
    )
    total_xg  = rows.total_xg or 0.0
    total_min = rows.total_min or 0
    if total_min < PROP_MIN_MINUTES:
        return None
    return float(total_xg) / float(total_min) * 90.0


def team_per_90_xg(
    db: Session,
    team_name: str,
    lookback_days: int = PROP_LOOKBACK_DAYS,
) -> Optional[float]:
    """Team-level xG per 90 — used to normalise per-player rates by match context."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(
            func.sum(PlayerMatchStats.xg).label("total_xg"),
            func.count(func.distinct(PlayerMatchStats.match_id)).label("matches"),
        )
        .filter(
            PlayerMatchStats.team == team_name,
            PlayerMatchStats.match_date >= cutoff,
            PlayerMatchStats.xg.isnot(None),
        )
        .one()
    )
    if not rows.matches:
        return None
    return float(rows.total_xg or 0.0) / float(rows.matches)


def predict_scorer_props(
    db: Session,
    player_name: str,
    player_team: str,
    expected_minutes: int = DEFAULT_MINUTES,
    match_lambda_for_team: Optional[float] = None,
    match_lambda_for_opponent: Optional[float] = None,
) -> Optional[dict]:
    """
    Returns scorer probabilities for a single player in a single upcoming match.
    Pass `match_lambda_for_team` from the DC model (expected goals for that team)
    so the player's per-90 rate gets calibrated to the match xG total.
    """
    per_90 = player_per_90_xg(db, player_name)
    if per_90 is None or per_90 <= 0:
        return None

    lam_player = per_90 * (expected_minutes / 90.0)

    # Scale by match context: if team's expected match xG is materially different
    # from their per-90 norm, scale player rate proportionally.
    if match_lambda_for_team is not None and match_lambda_for_team > 0:
        team_avg = team_per_90_xg(db, player_team)
        if team_avg and team_avg > 0:
            ratio = match_lambda_for_team / team_avg
            # Clamp ratio to avoid runaway when team has tiny sample
            ratio = max(0.4, min(2.5, ratio))
            lam_player *= ratio

    p_no_goal     = math.exp(-lam_player)
    p_one_goal    = lam_player * math.exp(-lam_player)
    p_two_plus    = max(0.0, 1.0 - p_no_goal - p_one_goal)
    p_anytime     = 1.0 - p_no_goal

    # First-scorer approximation: player's share of total match xG × P(any goal)
    p_first = None
    if match_lambda_for_team is not None and match_lambda_for_opponent is not None:
        total_lam = match_lambda_for_team + match_lambda_for_opponent
        if total_lam > 0:
            p_any_goal_match = 1.0 - math.exp(-total_lam)
            p_first = (lam_player / total_lam) * p_any_goal_match

    return {
        "player":           player_name,
        "team":             player_team,
        "expected_minutes": expected_minutes,
        "xg_per_90":        round(per_90, 3),
        "xg_match":         round(lam_player, 3),
        "anytime_scorer":   round(p_anytime, 4),
        "two_plus":         round(p_two_plus, 4),
        "first_scorer":     round(p_first, 4) if p_first is not None else None,
    }


def _is_national_team(db: Session, team_name: str) -> bool:
    """Heuristic: a team is a nation iff we have a NationalTeamSquad row for it."""
    return db.query(NationalTeamSquad).filter(NationalTeamSquad.nation == team_name).first() is not None


def _candidates_for_club(db: Session, team_name: str, cutoff) -> list:
    """Players who suited up for this club with enough minutes."""
    return (
        db.query(
            PlayerMatchStats.player_name,
            func.sum(PlayerMatchStats.minutes).label("total_min"),
            func.sum(PlayerMatchStats.xg).label("total_xg"),
        )
        .filter(
            PlayerMatchStats.team == team_name,
            PlayerMatchStats.match_date >= cutoff,
            PlayerMatchStats.minutes.isnot(None),
        )
        .group_by(PlayerMatchStats.player_name)
        .having(func.sum(PlayerMatchStats.minutes) >= PROP_MIN_MINUTES)
        .order_by(desc("total_xg"))
        .limit(50)
        .all()
    )


def _candidates_for_nation(db: Session, nation: str, cutoff) -> list:
    """
    Players in the national squad whose CLUB-level PMS aggregate meets the
    minimum-minutes bar. Returns rows shaped like _candidates_for_club so the
    downstream loop is uniform.
    """
    squad_rows = (
        db.query(NationalTeamSquad.player_name)
        .filter(NationalTeamSquad.nation == nation)
        .all()
    )
    if not squad_rows:
        return []
    names = [r.player_name for r in squad_rows]

    return (
        db.query(
            PlayerMatchStats.player_name,
            func.sum(PlayerMatchStats.minutes).label("total_min"),
            func.sum(PlayerMatchStats.xg).label("total_xg"),
        )
        .filter(
            PlayerMatchStats.player_name.in_(names),
            PlayerMatchStats.match_date >= cutoff,
            PlayerMatchStats.minutes.isnot(None),
        )
        .group_by(PlayerMatchStats.player_name)
        .having(func.sum(PlayerMatchStats.minutes) >= PROP_MIN_MINUTES)
        .order_by(desc("total_xg"))
        .limit(50)
        .all()
    )


def top_props_for_team(
    db: Session,
    team_name: str,
    match_lambda_for_team: Optional[float] = None,
    match_lambda_for_opponent: Optional[float] = None,
    top_n: int = 5,
    lookback_days: int = PROP_LOOKBACK_DAYS,
) -> list[dict]:
    """
    Returns the top N likely scorers for this team in an upcoming match,
    ranked by anytime-scorer probability.

    For CLUB teams: pulls candidates from PlayerMatchStats.team match.
    For NATIONAL teams: resolves the squad via NationalTeamSquad, then pulls
    candidate players' CLUB-level PMS rows for rate estimation. Per-90 rates
    from the player's club season are usually a better signal than sparse
    intl matches anyway.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    if _is_national_team(db, team_name):
        candidates = _candidates_for_nation(db, team_name, cutoff)
    else:
        candidates = _candidates_for_club(db, team_name, cutoff)

    results = []
    for row in candidates:
        props = predict_scorer_props(
            db, row.player_name, team_name,
            expected_minutes=DEFAULT_MINUTES,
            match_lambda_for_team=match_lambda_for_team,
            match_lambda_for_opponent=match_lambda_for_opponent,
        )
        if props:
            results.append(props)
        if len(results) >= top_n:
            break

    results.sort(key=lambda r: r["anytime_scorer"], reverse=True)
    return results[:top_n]
