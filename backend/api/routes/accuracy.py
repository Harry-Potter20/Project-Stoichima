from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import Prediction, Match
from datetime import datetime

router = APIRouter()


def _resolve_predictions(db: Session):
    """Fill in actuals for any stored predictions whose match is now FINISHED."""
    unresolved = db.query(Prediction).filter(Prediction.actual_outcome == None).all()
    for pred in unresolved:
        match = db.query(Match).filter(
            Match.home_team == pred.home_team,
            Match.away_team == pred.away_team,
            Match.match_date == pred.match_date,
            Match.status == "FINISHED",
        ).first()
        if match and match.result is not None:
            pred.actual_outcome  = match.result
            pred.actual_over_2_5 = match.over_2_5_goals
            pred.actual_btts     = match.btts
            pred.resolved_at     = datetime.utcnow()
    db.commit()


@router.get("/accuracy/{competition_id}")
def get_accuracy(competition_id: str, db: Session = Depends(get_db)):
    _resolve_predictions(db)

    resolved = db.query(Prediction).filter(
        Prediction.competition == competition_id,
        Prediction.actual_outcome != None,
    ).all()

    if not resolved:
        return {"competition": competition_id, "resolved": 0, "accuracy": {}}

    total = len(resolved)
    outcome_correct  = sum(1 for p in resolved if p.predicted_outcome == p.actual_outcome)
    over25_correct   = sum(1 for p in resolved if p.predicted_over_2_5 == p.actual_over_2_5)
    btts_correct     = sum(1 for p in resolved if p.predicted_btts == p.actual_btts)

    # Per-class breakdown (H / D / A)
    per_class = {}
    for label in ["H", "D", "A"]:
        subset = [p for p in resolved if p.actual_outcome == label]
        if subset:
            correct = sum(1 for p in subset if p.predicted_outcome == label)
            per_class[label] = {"total": len(subset), "correct": correct, "accuracy": round(correct / len(subset), 4)}

    return {
        "competition": competition_id,
        "resolved": total,
        "accuracy": {
            "outcome":  round(outcome_correct / total, 4),
            "over_2_5": round(over25_correct   / total, 4),
            "btts":     round(btts_correct     / total, 4),
        },
        "per_class": per_class,
    }
