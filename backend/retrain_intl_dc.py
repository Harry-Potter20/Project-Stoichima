"""
Prototype: Elo-seeded Dixon-Coles for international football.

Fits the international-only DC model two ways — flat-mean shrinkage (baseline)
vs Elo-seeded priors — and prints a side-by-side lambda comparison for the
upcoming WC fixtures, then saves the Elo-seeded model as the live artifact
(backing up the previous one first).

Run from backend/:
    python retrain_intl_dc.py
"""
import os
import shutil
from datetime import datetime

import pandas as pd

from app.database import SessionLocal
from app.models import Match, EloRating
from models.goals_distribution_model import GoalsDistributionModel

INTL_CODES = ["WC", "EC", "UNL", "AFC", "ASIA", "CA", "GOLD"]
ARTIFACT = "saved_models/goals_distribution_intl.pkl"


def _build_intl_df(db) -> pd.DataFrame:
    rows = db.query(Match).filter(
        Match.status == "FINISHED",
        Match.competition.in_(INTL_CODES),
        Match.home_team_score.isnot(None),
        Match.away_team_score.isnot(None),
    ).all()
    return pd.DataFrame([{
        "home_team":        m.home_team,
        "away_team":        m.away_team,
        "match_date":       m.match_date,
        "home_team_score":  m.home_team_score,
        "away_team_score":  m.away_team_score,
        "is_neutral_venue": 1,
    } for m in rows])


def _elo_priors(db) -> dict[str, float]:
    rows = db.query(EloRating).filter(EloRating.competition.is_(None)).all()
    return {r.team: float(r.elo) for r in rows if r.elo is not None}


def _upcoming_wc(db, limit=10):
    return db.query(Match).filter(
        Match.competition == "WC",
        Match.status.in_(["SCHEDULED", "TIMED"]),
    ).order_by(Match.match_date).limit(limit).all()


def main():
    db = SessionLocal()
    try:
        intl_df = _build_intl_df(db)
        priors  = _elo_priors(db)
        fixtures = _upcoming_wc(db)
    finally:
        db.close()

    print(f"International matches: {len(intl_df)} | Elo priors: {len(priors)} teams\n")
    if len(intl_df) < 100:
        print("Too few international matches to fit (need >=100). Aborting.")
        return

    baseline = GoalsDistributionModel(); baseline.fit(intl_df)
    seeded   = GoalsDistributionModel(); seeded.fit(intl_df, elo_priors=priors)

    print(f"elo_prior_used: baseline={baseline.elo_prior_used} seeded={seeded.elo_prior_used}\n")
    print(f"{'fixture':<34}{'baseline λ':>16}{'elo-seeded λ':>18}")
    print("-" * 68)
    for m in fixtures:
        bh, ba = baseline.get_lambdas(m.home_team, m.away_team, neutral_venue=True)
        sh, sa = seeded.get_lambdas(m.home_team, m.away_team, neutral_venue=True)
        name = f"{m.home_team[:15]} v {m.away_team[:15]}"
        print(f"{name:<34}{f'{bh:.2f}-{ba:.2f}':>16}{f'{sh:.2f}-{sa:.2f}':>18}")

    # Save the Elo-seeded model as the live artifact (backup first)
    if os.path.exists(ARTIFACT):
        bak = f"{ARTIFACT}.bak-{datetime.now():%Y%m%d%H%M%S}"
        shutil.copy2(ARTIFACT, bak)
        print(f"\nBacked up existing model → {bak}")
    seeded.save(ARTIFACT)
    print(f"Saved Elo-seeded model → {ARTIFACT}")


if __name__ == "__main__":
    main()
