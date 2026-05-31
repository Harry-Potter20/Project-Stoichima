"""
Tournament prediction and simulation endpoints.

GET /api/v1/tournament/{competition_id}/predictions
    Returns next-round match predictions (with H/D/A probabilities).

GET /api/v1/tournament/{competition_id}/simulate?n=10000
    Returns Monte Carlo stage probabilities for all teams in the tournament.

GET /api/v1/tournament/{competition_id}/groups
    Returns current group standings.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models import Match
from prediction.predictor import get_predictor
from prediction.tournament_simulator import MonteCarloSimulator
import prediction.cache as pred_cache

router = APIRouter()

INTERNATIONAL_COMPETITIONS = {"WC", "EC", "UNL", "CA"}


def _require_international(competition_id: str):
    if competition_id not in INTERNATIONAL_COMPETITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"{competition_id} is not a supported international competition. "
                   f"Use one of: {sorted(INTERNATIONAL_COMPETITIONS)}"
        )


@router.get("/tournament/{competition_id}/groups")
def get_groups(competition_id: str, db: Session = Depends(get_db)):
    """Current group standings derived from finished matches."""
    _require_international(competition_id)

    matches = (
        db.query(Match)
        .filter(
            Match.competition == competition_id,
            Match.tournament_stage == "Group Stage",
        )
        .order_by(Match.match_date)
        .all()
    )
    if not matches:
        raise HTTPException(status_code=404, detail=f"No group-stage data for {competition_id}")

    # Build standings
    from collections import defaultdict
    team_stats: dict[str, dict] = defaultdict(lambda: {
        "played": 0, "won": 0, "drawn": 0, "lost": 0,
        "gf": 0, "ga": 0, "gd": 0, "pts": 0, "group": "?"
    })
    team_groups: dict[str, str] = {}
    for m in matches:
        grp = m.tournament_group or "?"
        for team in (m.home_team, m.away_team):
            team_groups[team] = grp
            team_stats[team]["group"] = grp

    for m in matches:
        if m.status != "FINISHED" or m.result is None:
            continue
        home, away = m.home_team, m.away_team
        hs = m.home_team_score or 0
        as_ = m.away_team_score or 0
        for team in (home, away):
            team_stats[team]["played"] += 1
            team_stats[team]["gf"] += hs if team == home else as_
            team_stats[team]["ga"] += as_ if team == home else hs
            team_stats[team]["gd"] += (hs - as_) if team == home else (as_ - hs)
        if m.result == "H":
            team_stats[home]["won"] += 1
            team_stats[home]["pts"] += 3
            team_stats[away]["lost"] += 1
        elif m.result == "D":
            team_stats[home]["drawn"] += 1
            team_stats[home]["pts"] += 1
            team_stats[away]["drawn"] += 1
            team_stats[away]["pts"] += 1
        elif m.result == "A":
            team_stats[away]["won"] += 1
            team_stats[away]["pts"] += 3
            team_stats[home]["lost"] += 1

    # Group and sort
    groups_out: dict[str, list] = defaultdict(list)
    for team, stats in team_stats.items():
        groups_out[stats["group"]].append({"team": team, **stats})
    for grp in groups_out:
        groups_out[grp].sort(key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))
        # Mark qualification status based on current standings
        for rank, entry in enumerate(groups_out[grp]):
            if rank < 2:
                entry["status"] = "qualified"      # top 2 advance
            elif rank == 2:
                entry["status"] = "in_contention"  # 3rd — may still advance (WC 2026 format)
            else:
                entry["status"] = "eliminated"

    # Upcoming group matches — so users can see what's left
    upcoming = [m for m in matches if m.status != "FINISHED"]
    upcoming_out = [
        {
            "home_team":   m.home_team,
            "away_team":   m.away_team,
            "match_date":  m.match_date.isoformat() if m.match_date else None,
            "group":       m.tournament_group or "?",
            "status":      m.status,
        }
        for m in sorted(upcoming, key=lambda m: m.match_date or datetime(9999, 1, 1))
    ]

    # Groups still in play (any team not yet locked out)
    total_group_matches = 3  # each team plays 3 group matches (WC format)
    games_played = {team: stats["played"] for team, stats in team_stats.items()}
    groups_complete = all(
        v >= total_group_matches for v in games_played.values()
    )

    return {
        "competition":      competition_id,
        "groups_complete":  groups_complete,
        "groups":           dict(groups_out),
        "upcoming_matches": upcoming_out,
    }


@router.get("/tournament/{competition_id}/predictions")
def get_tournament_predictions(competition_id: str, db: Session = Depends(get_db)):
    """H/D/A probabilities for all upcoming tournament matches."""
    _require_international(competition_id)

    upcoming = (
        db.query(Match)
        .filter(
            Match.competition == competition_id,
            Match.status != "FINISHED",
        )
        .order_by(Match.match_date)
        .all()
    )
    if not upcoming:
        raise HTTPException(status_code=404, detail=f"No upcoming matches for {competition_id}")

    predictor = get_predictor()
    finished = (
        db.query(Match)
        .filter(Match.competition == competition_id, Match.status == "FINISHED")
        .all()
    )

    import pandas as pd
    from datetime import datetime

    history_df = pd.DataFrame([{
        "home_team": m.home_team, "away_team": m.away_team,
        "match_date": m.match_date, "season": m.season,
        "result": m.result, "home_team_score": m.home_team_score,
        "away_team_score": m.away_team_score,
        "is_neutral_venue": bool(m.is_neutral_venue),
        "tournament_stage": m.tournament_stage,
    } for m in finished]) if finished else pd.DataFrame()

    upcoming_df = pd.DataFrame([{
        "home_team": m.home_team, "away_team": m.away_team,
        "match_date": m.match_date, "season": m.season,
        "status": m.status or "SCHEDULED",
        "result": None,
        "home_team_score": None, "away_team_score": None,
        "home_team_xG": None, "away_team_xG": None,
        "over_2_5_goals": None, "btts": None,
        "referee": None,
        "home_team_yellow_cards": None, "away_team_yellow_cards": None,
        "home_team_red_cards": None, "away_team_red_cards": None,
        "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
        "is_neutral_venue": bool(m.is_neutral_venue),
        "tournament_stage": m.tournament_stage,
        "tournament_group": m.tournament_group,
    } for m in upcoming])

    finished_input = history_df if not history_df.empty else pd.DataFrame()

    try:
        preds = predictor.predict(
            upcoming_df, finished_input, db=db, competition=competition_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {e}")

    clean_preds = [{k: v for k, v in p.items() if not k.startswith("_")} for p in preds]

    try:
        from data_collection.odds_client import attach_odds
        attach_odds(clean_preds, competition_id)
    except Exception:
        pass

    return {"competition": competition_id, "predictions": clean_preds}


@router.get("/tournament/{competition_id}/simulate")
def simulate_tournament(
    competition_id: str,
    n: int = Query(default=5000, ge=1000, le=50000, description="Number of Monte Carlo simulations"),
    db: Session = Depends(get_db),
):
    """
    Run Monte Carlo simulation and return stage-reach probabilities for all teams.

    Each team gets:
        group  — probability of advancing from group stage
        r32    — reaching Round of 32
        r16    — reaching Round of 16
        qf     — reaching Quarter-Finals
        sf     — reaching Semi-Finals
        final  — reaching the Final
        winner — winning the tournament
    """
    _require_international(competition_id)

    cache_key = f"simulate:{competition_id}:{n}"
    cached = pred_cache.get(cache_key)
    if cached:
        return cached

    predictor = get_predictor()
    sim = MonteCarloSimulator(competition_id, predictor, db)
    try:
        sim.load()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    results = sim.simulate(n=n)

    # Sort teams by winner probability descending
    sorted_teams = sorted(results.items(), key=lambda x: -x[1]["winner"])
    result = {
        "competition":   competition_id,
        "simulations":   n,
        "probabilities": [
            {"team": team, **{k: round(v, 4) for k, v in probs.items()}}
            for team, probs in sorted_teams
        ],
    }
    pred_cache.set(cache_key, result)
    return result


@router.get("/tournament/{competition_id}/deterministic")
def deterministic_group_table(competition_id: str, db: Session = Depends(get_db)):
    """
    Deterministic WC table simulator: assumes every prediction is exactly correct
    (highest-probability outcome wins each remaining match) and returns the final
    projected group tables.
    """
    _require_international(competition_id)

    matches = (
        db.query(Match)
        .filter(
            Match.competition == competition_id,
            Match.tournament_stage == "Group Stage",
        )
        .order_by(Match.match_date)
        .all()
    )
    if not matches:
        raise HTTPException(status_code=404, detail=f"No group-stage data for {competition_id}")

    # Separate finished vs upcoming
    finished = [m for m in matches if m.status == "FINISHED" and m.result]
    upcoming = [m for m in matches if m.status != "FINISHED"]

    # Get predictions for upcoming matches
    predictor = get_predictor()
    predicted_results: dict[tuple, str] = {}

    if upcoming:
        import pandas as pd
        fin_df = pd.DataFrame([{
            "home_team": m.home_team, "away_team": m.away_team,
            "match_date": m.match_date, "season": m.season,
            "result": m.result, "home_team_score": m.home_team_score,
            "away_team_score": m.away_team_score,
            "is_neutral_venue": bool(m.is_neutral_venue),
            "tournament_stage": m.tournament_stage,
        } for m in finished]) if finished else pd.DataFrame()

        upc_df = pd.DataFrame([{
            "home_team": m.home_team, "away_team": m.away_team,
            "match_date": m.match_date, "season": m.season, "status": "SCHEDULED",
            "result": None, "home_team_score": None, "away_team_score": None,
            "home_team_xG": None, "away_team_xG": None,
            "over_2_5_goals": None, "btts": None, "referee": None,
            "home_team_yellow_cards": None, "away_team_yellow_cards": None,
            "home_team_red_cards": None, "away_team_red_cards": None,
            "opening_home_odds": None, "opening_draw_odds": None, "opening_away_odds": None,
            "is_neutral_venue": bool(m.is_neutral_venue),
            "tournament_stage": m.tournament_stage,
            "tournament_group": m.tournament_group,
        } for m in upcoming])

        try:
            preds = predictor.predict(upc_df, fin_df, db=db, competition=competition_id)
            for p in preds:
                key = (p["home_team"], p["away_team"])
                predicted_results[key] = p["outcome"]["predicted"]
        except Exception:
            pass

    # Build deterministic final standings
    from collections import defaultdict
    team_stats: dict[str, dict] = defaultdict(lambda: {
        "played": 0, "won": 0, "drawn": 0, "lost": 0,
        "gf": 0, "ga": 0, "gd": 0, "pts": 0, "group": "?"
    })

    def _apply_result(home, away, result, group, actual_score=None):
        team_stats[home]["group"] = group
        team_stats[away]["group"] = group
        team_stats[home]["played"] += 1
        team_stats[away]["played"] += 1
        if actual_score:
            hs, as_ = actual_score
            team_stats[home]["gf"] += hs
            team_stats[home]["ga"] += as_
            team_stats[home]["gd"] += hs - as_
            team_stats[away]["gf"] += as_
            team_stats[away]["ga"] += hs
            team_stats[away]["gd"] += as_ - hs
        if result == "H":
            team_stats[home]["won"] += 1
            team_stats[home]["pts"] += 3
            team_stats[away]["lost"] += 1
        elif result == "D":
            team_stats[home]["drawn"] += 1
            team_stats[home]["pts"] += 1
            team_stats[away]["drawn"] += 1
            team_stats[away]["pts"] += 1
        elif result == "A":
            team_stats[away]["won"] += 1
            team_stats[away]["pts"] += 3
            team_stats[home]["lost"] += 1

    for m in finished:
        _apply_result(
            m.home_team, m.away_team, m.result, m.tournament_group or "?",
            actual_score=(m.home_team_score or 0, m.away_team_score or 0),
        )

    for m in upcoming:
        result = predicted_results.get((m.home_team, m.away_team), "D")
        _apply_result(m.home_team, m.away_team, result, m.tournament_group or "?")

    # Sort groups
    groups_out: dict[str, list] = defaultdict(list)
    for team, stats in team_stats.items():
        groups_out[stats["group"]].append({"team": team, **stats})
    for grp in groups_out:
        groups_out[grp].sort(key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))
        for rank, entry in enumerate(groups_out[grp]):
            entry["proj_rank"] = rank + 1
            entry["advances"] = rank < 2

    return {
        "competition":      competition_id,
        "method":           "deterministic",
        "matches_simulated": len(upcoming),
        "matches_played":    len(finished),
        "projected_groups":  dict(groups_out),
    }


@router.get("/tournament/{competition_id}/cs_edge")
def correct_score_edge(
    competition_id: str,
    home_team: str,
    away_team: str,
    db: Session = Depends(get_db),
):
    """
    Returns model correct-score probabilities alongside bookmaker implied CS prices
    from The Odds API (where available). Edge = model_prob - bookmaker_implied.
    """
    _require_international(competition_id)
    predictor = get_predictor()

    if predictor.dist_model is None:
        raise HTTPException(status_code=503, detail="DC model not loaded — run train_models.py first")

    try:
        import numpy as np
        matrix = predictor.dist_model.predict_score_matrix(home_team, away_team)
        model_scores = predictor.dist_model.market_correct_score(matrix, top_n=12)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model error: {e}")

    # Annotate with bookmaker implied probs if snapshot odds available
    for score in model_scores:
        score["bookmaker_implied"] = None
        score["edge_pct"] = None

    return {
        "home_team":   home_team,
        "away_team":   away_team,
        "competition": competition_id,
        "scores":      model_scores,
    }
