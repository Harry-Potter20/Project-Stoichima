"""
Tennis model training pipeline.

Run after importing match data:
    cd backend
    python3 data_collection/tennis_data_collector.py   # fetch ATP data
    python3 train_tennis.py                             # train model + compute ELOs

The trained model is saved to saved_models/tennis_model.pkl.
Surface-specific ELO ratings are persisted to the tennis_player_elo table.
"""

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import logging
import pandas as pd
from app.database import SessionLocal
from app.models import TennisMatch
from data_processing.tennis_feature_engineering import (
    build_tennis_features,
    compute_elo_ratings,
    save_elo_to_db,
)
from models.tennis_model import TennisModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _load_matches(tour: str = "ATP") -> pd.DataFrame:
    with SessionLocal() as db:
        rows = db.query(TennisMatch).filter(TennisMatch.tour == tour).all()

    return pd.DataFrame([{
        "tourney_date":   m.tourney_date,
        "tourney_name":   m.tourney_name,
        "tourney_level":  m.tourney_level,
        "surface":        m.surface,
        "round":          m.round,
        "player1_name":   m.player1_name,
        "player2_name":   m.player2_name,
        "player1_rank":   m.player1_rank,
        "player2_rank":   m.player2_rank,
        "player1_seed":   m.player1_seed,
        "player2_seed":   m.player2_seed,
        "winner":         m.winner,
        "p1_svpt":        m.p1_svpt,
        "p1_1st_in":      m.p1_1st_in,
        "p1_1st_won":     m.p1_1st_won,
        "p1_2nd_won":     m.p1_2nd_won,
        "p1_bp_saved":    m.p1_bp_saved,
        "p1_bp_faced":    m.p1_bp_faced,
        "p2_svpt":        m.p2_svpt,
        "p2_1st_in":      m.p2_1st_in,
        "p2_1st_won":     m.p2_1st_won,
        "p2_2nd_won":     m.p2_2nd_won,
        "p2_bp_saved":    m.p2_bp_saved,
        "p2_bp_faced":    m.p2_bp_faced,
    } for m in rows])


def run(tour: str = "ATP"):
    log.info("Loading %s matches from DB…", tour)
    df = _load_matches(tour)

    if df.empty:
        log.error("No tennis matches in DB. Run tennis_data_collector.py first.")
        return

    log.info("  %d matches loaded", len(df))

    # ── Compute and persist ELO ratings ──────────────────────────────────────
    log.info("Computing ELO ratings…")
    elo_ratings = compute_elo_ratings(df)
    with SessionLocal() as db:
        save_elo_to_db(elo_ratings, tour, db)
    log.info("  ELO saved for %d players", len(elo_ratings))

    # ── Build features ────────────────────────────────────────────────────────
    log.info("Building features…")
    feature_df = build_tennis_features(df)
    log.info("  Feature matrix: %d rows × %d cols", *feature_df.shape)

    # ── Train model ───────────────────────────────────────────────────────────
    log.info("Training TennisModel…")
    model = TennisModel()
    model.train(feature_df)
    metrics = model.evaluate(feature_df)
    model.save("saved_models/tennis_model.pkl")

    log.info(
        "  ✓ accuracy=%.3f  AUC=%.3f  Brier=%.4f  (test_n=%d)",
        metrics.get("accuracy", 0),
        metrics.get("auc", 0),
        metrics.get("brier", 0),
        metrics.get("test_n", 0),
    )
    log.info("Tennis model saved to saved_models/tennis_model.pkl")


if __name__ == "__main__":
    import sys
    tour = sys.argv[1].upper() if len(sys.argv) > 1 else "ATP"
    run(tour)
