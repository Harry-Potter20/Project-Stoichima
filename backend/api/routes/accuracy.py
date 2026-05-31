from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import Prediction, Match
from datetime import datetime
import numpy as np

router = APIRouter()


def _brier_score(probs_list: list[list[float]], actuals: list[str]) -> float:
    """Multi-class Brier score — lower is better. Baseline (uniform) = 0.667."""
    total = 0.0
    label_map = {"H": 0, "D": 1, "A": 2}
    for probs, actual in zip(probs_list, actuals):
        if actual not in label_map:
            continue
        one_hot = [0.0, 0.0, 0.0]
        one_hot[label_map[actual]] = 1.0
        total += sum((p - o) ** 2 for p, o in zip(probs, one_hot))
    return round(total / len(probs_list), 4) if probs_list else 0.0


def _resolve_predictions(db: Session, competition_id: str) -> None:
    """Fill in actuals for any stored predictions whose match is now FINISHED."""
    unresolved = (
        db.query(Prediction)
        .filter(
            Prediction.competition == competition_id,
            Prediction.actual_outcome == None,
        )
        .all()
    )
    if not unresolved:
        return

    # Batch-fetch all finished matches for this competition keyed by (home, away, date)
    finished = (
        db.query(Match)
        .filter(
            Match.competition == competition_id,
            Match.status == "FINISHED",
            Match.result != None,
        )
        .all()
    )
    match_lookup = {
        (m.home_team, m.away_team, str(m.match_date)[:10]): m
        for m in finished
    }

    updated = 0
    for pred in unresolved:
        key = (pred.home_team, pred.away_team, str(pred.match_date)[:10])
        match = match_lookup.get(key)
        if match:
            pred.actual_outcome  = match.result
            pred.actual_over_2_5 = match.over_2_5_goals
            pred.actual_btts     = match.btts
            pred.resolved_at     = datetime.utcnow()
            updated += 1

    if updated:
        db.commit()


@router.get("/accuracy/{competition_id}")
def get_accuracy(competition_id: str, db: Session = Depends(get_db)):
    _resolve_predictions(db, competition_id)

    resolved = db.query(Prediction).filter(
        Prediction.competition == competition_id,
        Prediction.actual_outcome != None,
    ).all()

    if not resolved:
        return {"competition": competition_id, "resolved": 0, "accuracy": {}}

    total = len(resolved)
    outcome_correct = sum(1 for p in resolved if p.predicted_outcome == p.actual_outcome)
    over25_correct  = sum(1 for p in resolved if p.predicted_over_2_5 == p.actual_over_2_5)
    btts_correct    = sum(1 for p in resolved if p.predicted_btts == p.actual_btts)

    per_class = {}
    for label in ["H", "D", "A"]:
        subset = [p for p in resolved if p.actual_outcome == label]
        if subset:
            correct = sum(1 for p in subset if p.predicted_outcome == label)
            per_class[label] = {
                "total":    len(subset),
                "correct":  correct,
                "accuracy": round(correct / len(subset), 4),
            }

    # Brier score — how calibrated are the probability estimates?
    probs_list = [
        [p.home_win_prob or 0.333, p.draw_prob or 0.333, p.away_win_prob or 0.333]
        for p in resolved
        if p.home_win_prob is not None
    ]
    actuals_for_brier = [
        p.actual_outcome for p in resolved if p.home_win_prob is not None
    ]
    brier = _brier_score(probs_list, actuals_for_brier)
    uniform_brier = round(2.0 / 3.0, 4)  # always-predict-1/3 baseline
    brier_skill   = round(1.0 - brier / uniform_brier, 4) if uniform_brier else 0.0

    # Rolling accuracy (last 10 resolved predictions)
    recent = sorted(resolved, key=lambda p: p.match_date or datetime.min)[-10:]
    recent_correct = sum(1 for p in recent if p.predicted_outcome == p.actual_outcome)
    rolling_acc = round(recent_correct / len(recent), 4) if recent else 0.0

    return {
        "competition": competition_id,
        "resolved":    total,
        "accuracy": {
            "outcome":       round(outcome_correct / total, 4),
            "over_2_5":      round(over25_correct   / total, 4),
            "btts":          round(btts_correct     / total, 4),
            "rolling_10":    rolling_acc,
        },
        "calibration": {
            "brier_score":       brier,
            "brier_baseline":    uniform_brier,
            "brier_skill_score": brier_skill,
        },
        "per_class": per_class,
    }
