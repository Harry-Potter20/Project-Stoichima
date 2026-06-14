"""
GET /api/v1/scoring/{competition_id}
GET /api/v1/scoring

Scores the model's 1X2 probabilities against the bookmaker closing line on
the SAME resolved matches — the only honest way to judge a prediction model.

Argmax "accuracy" is a poor metric for three-way markets (a perfectly
calibrated draw-heavy distribution scores badly because draws rarely clear
50%). Brier / log-loss versus the market answers the real question: are we
adding information the closing line doesn't already contain?

    brier_skill = 1 - model_brier / market_brier
        > 0  → model beats the closing line (genuine edge)
        ~ 0  → model matches the market (fine, but no edge)
        < 0  → model is worse than just trusting the bookmaker
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Prediction, Match
import numpy as np
import math

router = APIRouter()

_OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


def _implied_probs(home: float | None, draw: float | None, away: float | None):
    """De-vig decimal odds → [p_h, p_d, p_a]. Returns None if odds incomplete."""
    if not home or not draw or not away or home <= 1 or draw <= 1 or away <= 1:
        return None
    raw = [1.0 / home, 1.0 / draw, 1.0 / away]
    overround = sum(raw)
    if overround <= 0:
        return None
    return [r / overround for r in raw]


def _match_odds(m: Match):
    """Prefer market-average closing odds; fall back to Bet365."""
    return _implied_probs(m.avg_home, m.avg_draw, m.avg_away) \
        or _implied_probs(m.b365_home, m.b365_draw, m.b365_away)


def _multiclass_brier(prob_vecs: list[list[float]], actuals: list[int]) -> float | None:
    """Mean over samples of sum_k (p_k - y_k)^2. Range [0, 2]; lower is better."""
    if not prob_vecs:
        return None
    total = 0.0
    for probs, y in zip(prob_vecs, actuals):
        total += sum((p - (1.0 if k == y else 0.0)) ** 2 for k, p in enumerate(probs))
    return round(total / len(prob_vecs), 4)


def _log_loss(prob_vecs: list[list[float]], actuals: list[int]) -> float | None:
    if not prob_vecs:
        return None
    eps = 1e-15
    total = 0.0
    for probs, y in zip(prob_vecs, actuals):
        total += -math.log(min(max(probs[y], eps), 1 - eps))
    return round(total / len(prob_vecs), 4)


def _score_competition(comp: str, db: Session) -> dict | None:
    resolved = db.query(Prediction).filter(
        Prediction.competition == comp,
        Prediction.actual_outcome.isnot(None),
    ).all()
    if not resolved:
        return None

    # Index matches for odds lookup (competition + teams + date)
    matches = db.query(Match).filter(Match.competition == comp).all()
    odds_lut = {
        (m.home_team, m.away_team, str(m.match_date)[:10]): _match_odds(m)
        for m in matches
    }

    model_vecs, market_vecs, actuals = [], [], []
    n_correct_model = n_correct_market = 0
    for p in resolved:
        y = _OUTCOME_INDEX.get(p.actual_outcome)
        if y is None:
            continue
        market = odds_lut.get((p.home_team, p.away_team, str(p.match_date)[:10]))
        if market is None:
            continue  # only score matches where we have a closing line to compare to
        model = [p.home_win_prob, p.draw_prob, p.away_win_prob]
        model_vecs.append(model)
        market_vecs.append(market)
        actuals.append(y)
        if int(np.argmax(model)) == y:
            n_correct_model += 1
        if int(np.argmax(market)) == y:
            n_correct_market += 1

    n = len(actuals)
    if n == 0:
        return {
            "competition": comp,
            "n_with_odds": 0,
            "n_resolved": len(resolved),
            "note": "No resolved matches have closing odds to score against yet.",
        }

    model_brier = _multiclass_brier(model_vecs, actuals)
    market_brier = _multiclass_brier(market_vecs, actuals)
    skill = (
        round(1 - model_brier / market_brier, 4)
        if model_brier is not None and market_brier else None
    )

    return {
        "competition":         comp,
        "n_with_odds":         n,
        "n_resolved":          len(resolved),
        "model_brier":         model_brier,
        "market_brier":        market_brier,
        "brier_skill_vs_market": skill,
        "beats_market":        (skill is not None and skill > 0),
        "model_log_loss":      _log_loss(model_vecs, actuals),
        "market_log_loss":     _log_loss(market_vecs, actuals),
        "model_accuracy":      round(n_correct_model / n, 4),
        "market_accuracy":     round(n_correct_market / n, 4),
        "interpretation":      _interpret(skill, n),
    }


def _interpret(skill: float | None, n: int) -> str:
    if skill is None:
        return "Not enough data."
    sample = "" if n >= 50 else f" (only {n} matches — treat as provisional; needs ~50+ to be reliable)"
    if skill > 0.02:
        return f"Model beats the closing line by {skill*100:.1f}% Brier skill{sample}."
    if skill > -0.02:
        return f"Model is level with the market{sample}."
    return f"Model trails the closing line by {abs(skill)*100:.1f}%{sample}."


@router.get("/scoring/{competition_id}")
def get_scoring(competition_id: str, db: Session = Depends(get_db)):
    result = _score_competition(competition_id, db)
    if result is None:
        return {"competition": competition_id, "n_resolved": 0, "n_with_odds": 0,
                "note": "No resolved predictions yet."}
    return result


@router.get("/scoring")
def get_scoring_all(db: Session = Depends(get_db)):
    from sqlalchemy import distinct
    comps = [
        row[0] for row in db.query(distinct(Prediction.competition))
        .filter(Prediction.actual_outcome.isnot(None)).all()
    ]
    results = [r for c in comps if (r := _score_competition(c, db)) and r.get("n_with_odds")]
    results.sort(key=lambda r: (r.get("brier_skill_vs_market") is None,
                                -(r.get("brier_skill_vs_market") or 0)))
    return {"competitions": results}
