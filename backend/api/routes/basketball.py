"""
Basketball prediction endpoint using ELO ratings.

GET /api/v1/basketball/predict
  ?home_team=<name>&away_team=<name>&competition=<NBA|EuroLeague|...>

ELO-based win probability with home-court advantage (+75 ELO points).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import BasketballTeamElo, BasketballMatch
import math

router = APIRouter()

HOME_COURT_ADVANTAGE = 75.0   # ELO bonus for home team
DEFAULT_ELO          = 1500.0
K_FACTOR             = 20.0   # ELO update magnitude per game


def _elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Expected win probability for team A vs team B."""
    return 1.0 / (1.0 + math.pow(10.0, (elo_b - elo_a) / 400.0))


def _get_or_create_elo(db: Session, team: str, competition: str) -> BasketballTeamElo:
    record = db.query(BasketballTeamElo).filter(
        BasketballTeamElo.team == team,
        BasketballTeamElo.competition == competition,
    ).first()
    if not record:
        record = BasketballTeamElo(team=team, competition=competition, elo=DEFAULT_ELO, matches=0)
        db.add(record)
        db.commit()
        db.refresh(record)
    return record


@router.get("/basketball/predict")
def predict_basketball(
    home_team:   str = Query(..., description="Home team name"),
    away_team:   str = Query(..., description="Away team name"),
    competition: str = Query("NBA", description="Competition code"),
    db: Session = Depends(get_db),
):
    """ELO-based win probability for a basketball matchup."""
    home_rec = _get_or_create_elo(db, home_team, competition)
    away_rec = _get_or_create_elo(db, away_team, competition)

    home_elo_adj = home_rec.elo + HOME_COURT_ADVANTAGE
    p_home = _elo_win_prob(home_elo_adj, away_rec.elo)
    p_away = round(1.0 - p_home, 4)
    p_home = round(p_home, 4)

    implied_home_odds = round(1.0 / p_home, 3) if p_home > 0 else None
    implied_away_odds = round(1.0 / p_away, 3) if p_away > 0 else None

    return {
        "competition":         competition,
        "home_team":           home_team,
        "away_team":           away_team,
        "home_elo":            round(home_rec.elo, 1),
        "away_elo":            round(away_rec.elo, 1),
        "home_court_bonus":    HOME_COURT_ADVANTAGE,
        "home_win_prob":       p_home,
        "away_win_prob":       p_away,
        "fair_home_odds":      implied_home_odds,
        "fair_away_odds":      implied_away_odds,
        "home_matches_played": home_rec.matches,
        "away_matches_played": away_rec.matches,
    }


@router.get("/basketball/ratings")
def get_basketball_ratings(
    competition: str = Query("NBA"),
    db: Session = Depends(get_db),
):
    """All ELO ratings for a competition, sorted highest first."""
    records = (
        db.query(BasketballTeamElo)
        .filter(BasketballTeamElo.competition == competition)
        .order_by(BasketballTeamElo.elo.desc())
        .all()
    )
    return {
        "competition": competition,
        "teams": [
            {
                "rank":    i + 1,
                "team":    r.team,
                "elo":     round(r.elo, 1),
                "matches": r.matches,
            }
            for i, r in enumerate(records)
        ],
    }


@router.post("/basketball/result")
def record_basketball_result(
    home_team:   str,
    away_team:   str,
    home_score:  int,
    away_score:  int,
    competition: str = "NBA",
    season:      str = "",
    db: Session = Depends(get_db),
):
    """
    Record a finished game and update ELO ratings.
    Returns updated ELO values for both teams.
    """
    home_rec = _get_or_create_elo(db, home_team, competition)
    away_rec = _get_or_create_elo(db, away_team, competition)

    home_elo_adj = home_rec.elo + HOME_COURT_ADVANTAGE
    expected_home = _elo_win_prob(home_elo_adj, away_rec.elo)

    actual_home = 1.0 if home_score > away_score else 0.5 if home_score == away_score else 0.0
    actual_away = 1.0 - actual_home

    home_rec.elo     = round(home_rec.elo + K_FACTOR * (actual_home - expected_home), 2)
    away_rec.elo     = round(away_rec.elo + K_FACTOR * (actual_away - (1.0 - expected_home)), 2)
    home_rec.matches += 1
    away_rec.matches += 1

    result_code = "H" if home_score > away_score else "A" if away_score > home_score else "D"
    match = BasketballMatch(
        home_team=home_team, away_team=away_team,
        competition=competition, season=season,
        status="FINISHED", home_score=home_score, away_score=away_score,
        result=result_code,
        home_elo=home_rec.elo, away_elo=away_rec.elo,
    )
    db.add(match)
    db.commit()

    return {
        "result":    result_code,
        "home_elo":  home_rec.elo,
        "away_elo":  away_rec.elo,
    }
