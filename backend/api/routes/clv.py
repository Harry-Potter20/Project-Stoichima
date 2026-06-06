"""
GET /api/v1/clv/{competition_id}

Closing Line Value analysis — compares model predictions against historical
bookmaker closing odds for resolved predictions.

CLV = model_prob - (1 / closing_odds_vig_removed)

Positive CLV means the model identified value the market eventually corrected
toward. Sustained positive CLV is the strongest signal that a model has
genuine predictive alpha.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from app.database import get_db
from app.models import Prediction, Match, BetLog
import numpy as np

router = APIRouter()


def _vig_remove(h: float, d: float, a: float) -> tuple[float, float, float] | None:
    if not (h and d and a and h > 1 and d > 1 and a > 1):
        return None
    raw = [1/h, 1/d, 1/a]
    total = sum(raw)
    return tuple(v / total for v in raw)


@router.get("/clv/{competition_id}")
def get_clv(competition_id: str, db: Session = Depends(get_db)):
    resolved = (
        db.query(Prediction, Match)
        .join(Match, (
            (Match.home_team == Prediction.home_team) &
            (Match.away_team == Prediction.away_team) &
            (Match.match_date == Prediction.match_date)
        ), isouter=True)
        .filter(
            Prediction.competition == competition_id,
            Prediction.actual_outcome != None,
        )
        .all()
    )

    if not resolved:
        return {"competition": competition_id, "count": 0, "clv": None}

    clv_records = []
    for pred, match in resolved:
        # Try Pinnacle first (sharpest), then Bet365, then skip
        implied = None
        source = None
        if match:
            imp = _vig_remove(match.ps_home, match.ps_draw, match.ps_away)
            if imp:
                implied = imp
                source = "Pinnacle"
            else:
                imp = _vig_remove(match.b365_home, match.b365_draw, match.b365_away)
                if imp:
                    implied = imp
                    source = "Bet365"
            if not implied:
                imp = _vig_remove(match.avg_home, match.avg_draw, match.avg_away)
                if imp:
                    implied = imp
                    source = "Market avg"

        if implied is None:
            continue

        model_probs = [pred.home_win_prob, pred.draw_prob, pred.away_win_prob]
        clvs = [round((m - i) * 100, 2) for m, i in zip(model_probs, implied)]

        # CLV on the highest-edge outcome (what Kelly would have bet)
        best_idx = int(np.argmax(clvs))
        best_outcome = ["H", "D", "A"][best_idx]
        best_clv = clvs[best_idx]
        won = (pred.actual_outcome == best_outcome)

        clv_records.append({
            "match_date":   pred.match_date.date().isoformat() if pred.match_date else None,
            "home_team":    pred.home_team,
            "away_team":    pred.away_team,
            "bet_outcome":  best_outcome,
            "clv_pct":      best_clv,
            "won":          won,
            "actual":       pred.actual_outcome,
            "source":       source,
        })

    if not clv_records:
        return {"competition": competition_id, "count": 0, "clv": None,
                "note": "No historical odds available yet. Re-import CSVs to populate."}

    avg_clv    = round(float(np.mean([r["clv_pct"] for r in clv_records])), 3)
    pct_pos    = round(sum(1 for r in clv_records if r["clv_pct"] > 0) / len(clv_records) * 100, 1)
    win_rate   = round(sum(1 for r in clv_records if r["won"]) / len(clv_records) * 100, 1)

    # Running CLV over time (sorted by date)
    clv_records.sort(key=lambda x: x["match_date"] or "")
    running, cum = [], 0.0
    for r in clv_records:
        cum += r["clv_pct"]
        running.append({"date": r["match_date"], "cumulative_clv": round(cum, 2)})

    return {
        "competition": competition_id,
        "count":       len(clv_records),
        "summary": {
            "avg_clv_pct":    avg_clv,
            "pct_positive":   pct_pos,
            "win_rate_on_best_edge": win_rate,
        },
        "running": running,
        "records": clv_records[-50:],   # last 50 for table display
    }


@router.get("/clv")
def get_clv_summary(db: Session = Depends(get_db)):
    """
    Cross-competition CLV grade summary sourced from BetLog rows with stored clv_pct.

    For each competition with at least one settled bet that has closing odds:
      - avg_clv_pct: mean CLV across all bets
      - beat_close_pct: % of bets where we got better odds than closing
      - n_bets: sample size
      - win_rate: actual win rate on settled bets

    This auto-grades the model without requiring result actuals — CLV is the
    leading indicator; results are the lagging one.
    """
    competitions = [
        row[0] for row in db.query(distinct(BetLog.competition))
        .filter(BetLog.clv_pct.isnot(None))
        .all()
    ]

    rows = []
    for comp in competitions:
        bets = db.query(BetLog).filter(
            BetLog.competition == comp,
            BetLog.clv_pct.isnot(None),
        ).all()
        if not bets:
            continue
        clvs = [b.clv_pct for b in bets]
        settled = [b for b in bets if b.won is not None]
        rows.append({
            "competition":    comp,
            "n_bets":         len(bets),
            "avg_clv_pct":    round(float(np.mean(clvs)), 2),
            "beat_close_pct": round(sum(1 for c in clvs if c > 0) / len(clvs) * 100, 1),
            "win_rate":       round(sum(1 for b in settled if b.won) / len(settled) * 100, 1) if settled else None,
            "n_settled":      len(settled),
        })

    rows.sort(key=lambda r: r["avg_clv_pct"], reverse=True)
    return {"grades": rows}
