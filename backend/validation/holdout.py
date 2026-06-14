"""
Fast single-split holdout backtest, scored against the bookmaker closing line.

The walk-forward replayer (walk_forward.py) retrains per matchday and rebuilds
features over the full match history each time — intractable on a 50k+ row DB.
This module does ONE time-split instead:

  - train MatchOutcomeModel on scoped matches BEFORE a cutoff date
  - predict the target competition's matches AFTER the cutoff
  - score model vs de-vigged market odds, and sweep the market-blend weight to
    find the weight that minimises Brier (the value to put in
    settings.market_blend_weight)

Usage from backend/:
    python -m validation.holdout --competition PL --cutoff 2026-01-01
    python -m validation.holdout --competition PL --cutoff 2026-01-01 \
        --pool PL,PD,BL1,SA,FL1 --since 2023
"""
from __future__ import annotations
import argparse

import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.models import Match
from data_processing.feature_engineering import build_features
from data_processing.team_normalizer import normalise_team_name
from models.match_outcome_model import MatchOutcomeModel

IDX = {"H": 0, "D": 1, "A": 2}


def _load(pool: list[str], since: int) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = db.query(Match).filter(
            Match.status == "FINISHED",
            Match.competition.in_(pool),
            Match.season >= since,
        ).all()
    df = pd.DataFrame([{
        "home_team": normalise_team_name(m.home_team),
        "away_team": normalise_team_name(m.away_team),
        "match_date": m.match_date, "season": m.season, "competition": m.competition,
        "status": m.status, "result": m.result,
        "home_team_score": m.home_team_score, "away_team_score": m.away_team_score,
        "home_team_xG": m.home_team_xG, "away_team_xG": m.away_team_xG,
        "over_2_5_goals": m.over_2_5_goals, "btts": m.btts, "referee": m.referee,
        "is_neutral_venue": m.is_neutral_venue,
        "opening_home_odds": m.opening_home_odds, "opening_draw_odds": m.opening_draw_odds,
        "opening_away_odds": m.opening_away_odds,
        "b365_home": m.b365_home, "b365_draw": m.b365_draw, "b365_away": m.b365_away,
        "avg_home": m.avg_home, "avg_draw": m.avg_draw, "avg_away": m.avg_away,
        "home_team_yellow_cards": m.home_team_yellow_cards,
        "away_team_yellow_cards": m.away_team_yellow_cards,
        "home_team_red_cards": m.home_team_red_cards,
        "away_team_red_cards": m.away_team_red_cards,
    } for m in rows])
    df["match_date"] = pd.to_datetime(df["match_date"])
    return df


def _implied(h, d, a):
    try:
        h, d, a = float(h), float(d), float(a)
    except (TypeError, ValueError):
        return None
    # Reject NaN (nan comparisons are always False, so they'd slip past <=1)
    if any(x != x for x in (h, d, a)) or min(h, d, a) <= 1:
        return None
    raw = [1 / h, 1 / d, 1 / a]
    s = sum(raw)
    return [r / s for r in raw] if s else None


def _brier(p, y):
    return sum((p[k] - (1 if k == y else 0)) ** 2 for k in range(3))


def run(competition: str, cutoff: str, pool: list[str], since: int) -> dict:
    cut = pd.Timestamp(cutoff)
    full = _load(pool, since)
    print(f"Loaded {len(full)} finished matches ({'/'.join(pool)}, season >= {since})")

    feat = build_features(full)
    feat["match_date"] = pd.to_datetime(feat["match_date"])

    train = feat[feat["match_date"] < cut]
    test = feat[(feat["match_date"] >= cut) & (feat["competition"] == competition)]
    print(f"Train: {len(train)}  |  Test ({competition} after {cutoff}): {len(test)}")
    if len(train) < 500 or test.empty:
        raise SystemExit("Not enough data for a clean split.")

    model = MatchOutcomeModel()
    model.train(train)
    probs = model.predict_proba(test)

    samples = []
    for (_, row), p in zip(test.iterrows(), probs):
        y = IDX.get(row.get("result"))
        mk = _implied(row.get("avg_home"), row.get("avg_draw"), row.get("avg_away")) \
            or _implied(row.get("b365_home"), row.get("b365_draw"), row.get("b365_away"))
        if y is None or mk is None:
            continue
        samples.append(([float(p[0]), float(p[1]), float(p[2])], mk, y))

    n = len(samples)
    print(f"\nScored {n} test matches with odds\n")
    if n == 0:
        raise SystemExit("No test matches have odds to score against.")

    print(f"{'w(mkt)':>7}{'Brier':>9}{'acc':>7}")
    best = (0.0, 9.9)
    for i in range(0, 11):
        w = i / 10
        bs, ac = [], []
        for model_p, mk, y in samples:
            bl = [(1 - w) * model_p[k] + w * mk[k] for k in range(3)]
            bs.append(_brier(bl, y))
            ac.append(int(int(np.argmax(bl)) == y))
        mb = sum(bs) / n
        print(f"{w:>7.1f}{mb:>9.4f}{sum(ac)/n:>7.2f}")
        if mb < best[1]:
            best = (w, mb)

    model_brier = sum(_brier(m, y) for m, _, y in samples) / n
    market_brier = sum(_brier(mk, y) for _, mk, y in samples) / n
    skill = 1 - model_brier / market_brier if market_brier else None
    print(f"\nmodel Brier   {model_brier:.4f}")
    print(f"market Brier  {market_brier:.4f}")
    print(f"brier skill vs market (current model): {skill:+.4f}")
    print(f"optimal market_blend_weight: {best[0]}  (Brier {best[1]:.4f})")
    return {"n": n, "model_brier": model_brier, "market_brier": market_brier,
            "skill": skill, "optimal_weight": best[0]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", default="PL")
    ap.add_argument("--cutoff", default="2026-01-01")
    ap.add_argument("--pool", default="PL,PD,BL1,SA,FL1")
    ap.add_argument("--since", type=int, default=2023)
    args = ap.parse_args()
    run(args.competition, args.cutoff, args.pool.split(","), args.since)


if __name__ == "__main__":
    main()
