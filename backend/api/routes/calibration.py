"""
GET /api/v1/calibration/{competition_id}

Returns:
  - reliability curve: bins of predicted probability vs actual frequency
  - Brier score per market (outcome, over_2_5, btts)
  - Overall counts
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Prediction
import numpy as np

router = APIRouter()

N_BINS = 10  # 0-10%, 10-20%, …, 90-100%


def _reliability_curve(probs: list[float], actuals: list[int]) -> list[dict]:
    """
    Bin (prob, actual) pairs into N_BINS equal-width probability bins.
    Returns [{bin_center, predicted, actual, count}, …] — only non-empty bins.
    """
    if not probs:
        return []
    bins = np.linspace(0, 1, N_BINS + 1)
    curve = []
    for i in range(N_BINS):
        lo, hi = bins[i], bins[i + 1]
        mask = [lo <= p < hi for p in probs]
        if i == N_BINS - 1:
            mask = [lo <= p <= hi for p in probs]
        indices = [j for j, m in enumerate(mask) if m]
        if not indices:
            continue
        bin_probs   = [probs[j]   for j in indices]
        bin_actuals = [actuals[j] for j in indices]
        curve.append({
            "bin_center": round((lo + hi) / 2, 2),
            "predicted":  round(float(np.mean(bin_probs)),   4),
            "actual":     round(float(np.mean(bin_actuals)), 4),
            "count":      len(indices),
        })
    return curve


def _brier(probs: list[float], actuals: list[int]) -> float | None:
    if not probs:
        return None
    return round(float(np.mean([(p - a) ** 2 for p, a in zip(probs, actuals)])), 4)


@router.get("/calibration/{competition_id}")
def get_calibration(competition_id: str, db: Session = Depends(get_db)):
    resolved = db.query(Prediction).filter(
        Prediction.competition == competition_id,
        Prediction.actual_outcome != None,
    ).all()

    if not resolved:
        return {
            "competition": competition_id,
            "resolved": 0,
            "outcome": None,
            "over_2_5": None,
            "btts": None,
        }

    # ---- Outcome (H/D/A) — one reliability curve per class ----
    outcome_curves = {}
    for label, prob_attr in [("H", "home_win_prob"), ("D", "draw_prob"), ("A", "away_win_prob")]:
        probs   = [getattr(p, prob_attr) for p in resolved]
        actuals = [1 if p.actual_outcome == label else 0 for p in resolved]
        outcome_curves[label] = {
            "reliability": _reliability_curve(probs, actuals),
            "brier_score": _brier(probs, actuals),
        }

    # ---- Over 2.5 ----
    ou_resolved = [p for p in resolved if p.actual_over_2_5 is not None]
    ou_probs    = [p.over_2_5_prob for p in ou_resolved]
    ou_actuals  = [1 if p.actual_over_2_5 else 0 for p in ou_resolved]

    # ---- BTTS ----
    btts_resolved = [p for p in resolved if p.actual_btts is not None]
    btts_probs    = [p.btts_prob for p in btts_resolved]
    btts_actuals  = [1 if p.actual_btts else 0 for p in btts_resolved]

    return {
        "competition": competition_id,
        "resolved":    len(resolved),
        "outcome": {
            "per_class":   outcome_curves,
            "macro_brier": round(
                float(np.mean([v["brier_score"] for v in outcome_curves.values() if v["brier_score"] is not None])),
                4,
            ),
        },
        "over_2_5": {
            "reliability": _reliability_curve(ou_probs, ou_actuals),
            "brier_score": _brier(ou_probs, ou_actuals),
            "resolved":    len(ou_resolved),
        },
        "btts": {
            "reliability": _reliability_curve(btts_probs, btts_actuals),
            "brier_score": _brier(btts_probs, btts_actuals),
            "resolved":    len(btts_resolved),
        },
    }
