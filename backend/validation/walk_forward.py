"""
Walk-forward backtest for international tournaments.

Replays a historical tournament (WC22, EC24, etc.) one matchday at a time:
  - train on all matches BEFORE the matchday (no future leakage)
  - predict the matchday's fixtures
  - score predictions against actuals (accuracy + Brier + calibration)

Outputs a JSON report with per-matchday metrics + overall numbers so we can
project realistic accuracy expectations before WC 2026 kicks off.

Usage:
    python -m validation.walk_forward --competition WC --season 2022
    python -m validation.walk_forward --competition EC --season 2024
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.models import Match
from data_processing.feature_engineering import build_features
from data_processing.team_normalizer import normalise_team_name
from models.match_outcome_model import MatchOutcomeModel


def _load_match_df() -> pd.DataFrame:
    """Load all FINISHED matches into a DataFrame for feature engineering."""
    with SessionLocal() as db:
        rows = db.query(Match).filter(Match.status == "FINISHED").all()
    df = pd.DataFrame([{
        "home_team":         normalise_team_name(m.home_team),
        "away_team":         normalise_team_name(m.away_team),
        "match_date":        m.match_date,
        "season":            m.season,
        "competition":       m.competition,
        "status":            m.status,
        "home_team_score":   m.home_team_score,
        "away_team_score":   m.away_team_score,
        "home_team_xG":      m.home_team_xG,
        "away_team_xG":      m.away_team_xG,
        "result":            m.result,
        "over_2_5_goals":    m.over_2_5_goals,
        "btts":              m.btts,
        "referee":           m.referee,
        "is_neutral_venue":  m.is_neutral_venue,
        "tournament_stage":  m.tournament_stage,
        "opening_home_odds": m.opening_home_odds,
        "opening_draw_odds": m.opening_draw_odds,
        "opening_away_odds": m.opening_away_odds,
        "b365_home":         m.b365_home,
        "b365_draw":         m.b365_draw,
        "b365_away":         m.b365_away,
        "home_team_yellow_cards": m.home_team_yellow_cards,
        "away_team_yellow_cards": m.away_team_yellow_cards,
        "home_team_red_cards":    m.home_team_red_cards,
        "away_team_red_cards":    m.away_team_red_cards,
    } for m in rows])
    df["match_date"] = pd.to_datetime(df["match_date"])
    return df


def _brier_score(probs: np.ndarray, actual: str) -> float:
    """Multiclass Brier — probs ordered H,D,A; actual ∈ {H,D,A}."""
    target = {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}.get(actual)
    if target is None:
        return 0.0
    return float(sum((p - t) ** 2 for p, t in zip(probs, target)))


def walk_forward(competition: str, season: int, min_training: int = 1000) -> dict:
    """
    Replay tournament matches sequentially. For each matchday:
      - train MatchOutcomeModel on every match BEFORE the matchday
      - predict that matchday's fixtures
      - score predictions vs actuals
    """
    print(f"\n=== Walk-forward backtest: {competition} {season} ===")
    full_df = _load_match_df()
    print(f"Total finished matches loaded: {len(full_df)}")

    target_df = full_df[
        (full_df["competition"] == competition) & (full_df["season"] == season)
    ].sort_values("match_date").reset_index(drop=True)
    if target_df.empty:
        raise RuntimeError(f"No matches for {competition}/{season}")

    print(f"Target tournament has {len(target_df)} matches")

    # Group target matches by date (matchday)
    matchdays = sorted(target_df["match_date"].dt.date.unique())
    print(f"Distinct matchdays: {len(matchdays)}")

    overall = {
        "competition": competition, "season": season,
        "n_matches":   0, "n_correct":     0,
        "brier_sum":   0.0, "log_loss_sum": 0.0,
        "by_outcome":  {"H": [0, 0], "D": [0, 0], "A": [0, 0]},  # [correct, total]
        "matchdays":   [],
    }

    for md_date in matchdays:
        md_dt = pd.Timestamp(md_date)
        md_fixtures = target_df[target_df["match_date"].dt.date == md_date]

        # Training pool: every finished match strictly before this matchday
        train_pool = full_df[full_df["match_date"] < md_dt].copy()
        if len(train_pool) < min_training:
            print(f"  {md_date}: only {len(train_pool)} historical matches — skipping (need ≥{min_training})")
            continue

        # Engineer features for the training pool
        try:
            feat_df = build_features(train_pool)
        except Exception as e:
            print(f"  {md_date}: feature engineering failed: {e}")
            continue

        # Train an outcome model on this pool
        try:
            model = MatchOutcomeModel()
            model.train(feat_df)
        except Exception as e:
            print(f"  {md_date}: model train failed: {e}")
            continue

        # Predict this matchday — build features over combined (history + matchday)
        # so the matchday rows get features computed against the same training pool.
        md_input = md_fixtures.copy()
        md_input["status"] = "SCHEDULED"     # mask the result during feature build
        md_input["home_team_score"] = None
        md_input["away_team_score"] = None
        md_input["result"] = None

        combined = pd.concat([train_pool, md_input], ignore_index=True)
        try:
            combined_feat = build_features(combined)
        except Exception as e:
            print(f"  {md_date}: matchday feature build failed: {e}")
            continue

        md_feat = combined_feat[
            (combined_feat["match_date"].dt.date == md_date) &
            (combined_feat["competition"] == competition)
        ]
        if md_feat.empty:
            continue

        try:
            probs = model.predict_proba(md_feat)  # (n, 3) ordered H,D,A
            preds = model.predict(md_feat)        # H/D/A labels
        except Exception as e:
            print(f"  {md_date}: prediction failed: {e}")
            continue

        md_summary = {"date": str(md_date), "n_matches": len(md_feat), "rows": []}
        for i, (_, row) in enumerate(md_feat.iterrows()):
            actual = row["result"]
            # Look up actual from target_df (md_feat has it masked)
            actual_row = md_fixtures[
                (md_fixtures["home_team"] == row["home_team"]) &
                (md_fixtures["away_team"] == row["away_team"])
            ]
            actual = actual_row.iloc[0]["result"] if not actual_row.empty else None
            if actual not in ("H", "D", "A"):
                continue

            predicted = preds[i]
            prob_row = probs[i]
            correct = predicted == actual
            brier = _brier_score(prob_row, actual)
            # Log-loss safe (clip)
            actual_idx = {"H": 0, "D": 1, "A": 2}[actual]
            p_actual = max(prob_row[actual_idx], 1e-6)
            log_loss = -np.log(p_actual)

            overall["n_matches"]    += 1
            overall["n_correct"]    += int(correct)
            overall["brier_sum"]    += brier
            overall["log_loss_sum"] += log_loss
            c, t = overall["by_outcome"][actual]
            overall["by_outcome"][actual] = [c + int(correct), t + 1]

            md_summary["rows"].append({
                "home": row["home_team"], "away": row["away_team"],
                "predicted": predicted, "actual": actual,
                "probs": [round(float(p), 3) for p in prob_row],
                "correct": correct, "brier": round(brier, 4),
            })

        if md_summary["rows"]:
            n = len(md_summary["rows"])
            md_summary["accuracy"] = round(
                sum(1 for r in md_summary["rows"] if r["correct"]) / n, 3
            )
            md_summary["brier"]    = round(
                sum(r["brier"] for r in md_summary["rows"]) / n, 4
            )
            overall["matchdays"].append(md_summary)
            print(f"  {md_date}: {n} matches, acc={md_summary['accuracy']:.3f}, brier={md_summary['brier']:.4f}")

    # Compile overall metrics
    n = overall["n_matches"]
    if n:
        overall["accuracy"]    = round(overall["n_correct"] / n, 4)
        overall["brier"]       = round(overall["brier_sum"] / n, 4)
        overall["log_loss"]    = round(overall["log_loss_sum"] / n, 4)
        overall["brier_skill"] = round(1 - overall["brier"] / 0.6667, 4)
        for label, (c, t) in overall["by_outcome"].items():
            if t:
                overall["by_outcome"][label] = {"correct": c, "total": t,
                                                "rate": round(c / t, 3)}

    return overall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", required=True, help="e.g. WC, EC, AFC, ASIA, CA, GOLD, UNL")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--min-training", type=int, default=1000)
    ap.add_argument("--out", default="validation_report.json")
    args = ap.parse_args()

    result = walk_forward(args.competition, args.season, args.min_training)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out_path = args.out
    if not out_path.endswith(".json"):
        out_path = f"{out_path}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n=== Summary ({args.competition}/{args.season}) ===")
    print(f"Matches scored: {result.get('n_matches')}")
    print(f"Accuracy:       {result.get('accuracy', 0)*100:.1f}%")
    print(f"Brier:          {result.get('brier')}")
    print(f"Brier skill:    {result.get('brier_skill')}  (vs uniform 0.667)")
    print(f"Log-loss:       {result.get('log_loss')}")
    print(f"\nFull report: {out_path}")


if __name__ == "__main__":
    main()
