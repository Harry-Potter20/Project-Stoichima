"""
GET /api/v1/player-props/{competition}/{home}/{away}
  Returns top scorer + first-scorer + multi-goal probabilities for both teams.

GET /api/v1/player-props/by-player/{player_name}?team=...
  Single-player props for an upcoming match where the player's team is set.

The endpoint uses the DC model's expected lambdas to anchor per-player
predictions to the actual match xG context (vs raw season-long rates).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Match
from models.player_props_model import top_props_for_team, predict_scorer_props
from prediction.predictor import get_predictor

router = APIRouter()


def _resolve_lambdas(predictor, competition: str, home: str, away: str, neutral: bool):
    """Pick the right DC model and return (λ_home, λ_away)."""
    INTL = {
        "WC", "EC", "UNL", "AFC", "ASIA", "CA", "GOLD",
        "FRIENDLY", "WCQ_EU", "WCQ_AF", "WCQ_AS", "WCQ_SA", "WCQ_CC", "WCQ_OC",
    }
    dc = (
        predictor.dist_model_intl
        if competition in INTL and predictor.dist_model_intl is not None
        else predictor.dist_model
    )
    if dc is None:
        return None, None
    return dc.get_lambdas(home, away, neutral_venue=neutral)


@router.get("/player-props/{competition}/{home}/{away}")
def player_props_for_fixture(
    competition: str,
    home: str,
    away: str,
    top_n: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """
    Top N scorer probabilities for each side of a specific upcoming fixture.
    Uses DC-derived match xG as the per-player rate calibration anchor.
    """
    match = (
        db.query(Match)
        .filter(
            Match.competition == competition,
            Match.home_team   == home,
            Match.away_team   == away,
            Match.status.in_(["SCHEDULED", "TIMED"]),
        )
        .order_by(Match.match_date)
        .first()
    )
    if not match:
        raise HTTPException(404, f"No upcoming fixture {home} v {away} in {competition}")

    predictor = get_predictor()
    neutral = bool(match.is_neutral_venue)
    lam_h, lam_a = _resolve_lambdas(predictor, competition, home, away, neutral)

    home_props = top_props_for_team(
        db, home,
        match_lambda_for_team=lam_h,
        match_lambda_for_opponent=lam_a,
        top_n=top_n,
    )
    away_props = top_props_for_team(
        db, away,
        match_lambda_for_team=lam_a,
        match_lambda_for_opponent=lam_h,
        top_n=top_n,
    )
    return {
        "competition": competition,
        "home_team":   home,
        "away_team":   away,
        "match_date":  match.match_date.isoformat() if match.match_date else None,
        "expected_xg": {"home": lam_h, "away": lam_a},
        "home_props":  home_props,
        "away_props":  away_props,
    }
