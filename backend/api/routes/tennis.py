"""
Tennis prediction endpoints.

GET  /api/v1/tennis/players?q=federer        — player search (for UI autocomplete)
POST /api/v1/tennis/predict                  — predict a single match
GET  /api/v1/tennis/players/{name}/elo       — ELO profile for a player
GET  /api/v1/tennis/leaderboard?tour=ATP     — top 50 players by overall ELO

The model is loaded lazily on first request and cached in-process.
"""

import os
import joblib
import numpy as np
import pandas as pd
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import TennisPlayerElo, TennisMatch
from data_processing.tennis_feature_engineering import (
    TENNIS_FEATURES,
    _surface_key,
    build_tennis_features,
)

router = APIRouter()

MODEL_PATH = "saved_models/tennis_model.pkl"


@lru_cache(maxsize=1)
def _load_model():
    if not os.path.exists(MODEL_PATH):
        return None
    return joblib.load(MODEL_PATH)


def _get_player_elo(db: Session, name: str) -> TennisPlayerElo | None:
    # Exact match first, then case-insensitive substring
    p = db.query(TennisPlayerElo).filter(
        func.lower(TennisPlayerElo.player_name) == name.lower()
    ).first()
    if p:
        return p
    return db.query(TennisPlayerElo).filter(
        TennisPlayerElo.player_name.ilike(f"%{name}%")
    ).first()


def _build_inference_row(
    p1: TennisPlayerElo | None,
    p2: TennisPlayerElo | None,
    surface: str,
    p1_rank: int,
    p2_rank: int,
) -> dict:
    surf = _surface_key(surface)
    surf_col = surf.lower() if surf in ("Clay", "Grass", "Hard", "Carpet") else "hard"

    elo1_overall = p1.elo_overall if p1 else 1500.0
    elo2_overall = p2.elo_overall if p2 else 1500.0
    elo1_surf    = getattr(p1, f"elo_{surf.lower()}", 1500.0) if p1 else 1500.0
    elo2_surf    = getattr(p2, f"elo_{surf.lower()}", 1500.0) if p2 else 1500.0

    r1 = p1_rank or 200
    r2 = p2_rank or 200

    return {
        "elo_diff":              elo1_overall - elo2_overall,
        "surface_elo_diff":      elo1_surf    - elo2_surf,
        "rank_diff_log":         np.log(r2 / r1) if r1 > 0 and r2 > 0 else 0.0,
        "p1_form_win_rate":      0.5,   # unknown for ad-hoc queries; user can rely on ELO
        "p2_form_win_rate":      0.5,
        "form_diff":             0.0,
        "p1_surface_win_rate":   0.5,
        "p2_surface_win_rate":   0.5,
        "surface_wr_diff":       0.0,
        "h2h_p1_win_rate":       0.5,
        "h2h_surface_p1_wr":     0.5,
        "p1_serve_efficiency":   0.65,
        "p2_serve_efficiency":   0.65,
        "serve_diff":            0.0,
        "p1_return_efficiency":  0.35,
        "p2_return_efficiency":  0.35,
        "return_diff":           0.0,
        "is_grand_slam":         0,
        "is_masters":            0,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tennis/players")
def search_players(q: str = Query(default="", min_length=2), db: Session = Depends(get_db)):
    """Autocomplete player search for the match predictor UI."""
    results = (
        db.query(TennisPlayerElo)
        .filter(TennisPlayerElo.player_name.ilike(f"%{q}%"))
        .order_by(TennisPlayerElo.elo_overall.desc())
        .limit(15)
        .all()
    )
    return {
        "players": [
            {
                "name":           p.player_name,
                "tour":           p.tour,
                "elo_overall":    round(p.elo_overall, 0),
                "matches_played": p.matches_played,
            }
            for p in results
        ]
    }


@router.get("/tennis/players/{name}/elo")
def get_player_elo(name: str, db: Session = Depends(get_db)):
    p = _get_player_elo(db, name)
    if not p:
        raise HTTPException(404, detail=f"Player '{name}' not found")
    return {
        "name":           p.player_name,
        "tour":           p.tour,
        "elo_overall":    round(p.elo_overall, 1),
        "elo_clay":       round(p.elo_clay, 1),
        "elo_grass":      round(p.elo_grass, 1),
        "elo_hard":       round(p.elo_hard, 1),
        "elo_carpet":     round(p.elo_carpet, 1),
        "matches_played": p.matches_played,
        "as_of_date":     p.as_of_date.isoformat() if p.as_of_date else None,
    }


@router.get("/tennis/leaderboard")
def get_leaderboard(
    tour: str = "ATP",
    surface: str = "overall",
    db: Session = Depends(get_db),
):
    col_map = {
        "overall": TennisPlayerElo.elo_overall,
        "clay":    TennisPlayerElo.elo_clay,
        "grass":   TennisPlayerElo.elo_grass,
        "hard":    TennisPlayerElo.elo_hard,
    }
    order_col = col_map.get(surface.lower(), TennisPlayerElo.elo_overall)
    players = (
        db.query(TennisPlayerElo)
        .filter(TennisPlayerElo.tour == tour, TennisPlayerElo.matches_played >= 20)
        .order_by(order_col.desc())
        .limit(50)
        .all()
    )
    return {
        "tour":    tour,
        "surface": surface,
        "players": [
            {
                "rank":           i + 1,
                "name":           p.player_name,
                "elo_overall":    round(p.elo_overall, 1),
                "elo_clay":       round(p.elo_clay, 1),
                "elo_grass":      round(p.elo_grass, 1),
                "elo_hard":       round(p.elo_hard, 1),
                "matches_played": p.matches_played,
            }
            for i, p in enumerate(players)
        ],
    }


class MatchPredictRequest(BaseModel):
    player1: str
    player2: str
    surface: str = "Hard"
    player1_rank: int | None = None
    player2_rank: int | None = None
    is_grand_slam: bool = False
    is_masters:    bool = False


@router.post("/tennis/predict")
def predict_match(req: MatchPredictRequest, db: Session = Depends(get_db)):
    """
    Predict the winner of a tennis match given two player names and surface.
    Players are looked up by ELO rating; the model provides probability on top.
    """
    model = _load_model()

    p1 = _get_player_elo(db, req.player1)
    p2 = _get_player_elo(db, req.player2)

    # Build inference features
    row = _build_inference_row(
        p1, p2,
        surface=req.surface,
        p1_rank=req.player1_rank or (p1.matches_played and 100),
        p2_rank=req.player2_rank or (p2.matches_played and 100),
    )
    row["is_grand_slam"] = int(req.is_grand_slam)
    row["is_masters"]    = int(req.is_masters)

    if model is not None:
        df = pd.DataFrame([row])[TENNIS_FEATURES].fillna(0)
        p1_win_prob = float(model.predict_proba(df)[:, 1][0])
    else:
        # Fallback: ELO-only win probability
        elo1 = p1.elo_overall if p1 else 1500.0
        elo2 = p2.elo_overall if p2 else 1500.0
        p1_win_prob = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))

    p1_win_prob = round(p1_win_prob, 4)
    p2_win_prob = round(1.0 - p1_win_prob, 4)

    # ── Derived markets ────────────────────────────────────────────────────────
    p = p1_win_prob

    # Per-set win probability (surface ELO gives a finer signal than match ELO)
    surf_key = _surface_key(req.surface).lower()
    elo1_surf = getattr(p1, f"elo_{surf_key}", 1500.0) if p1 else 1500.0
    elo2_surf = getattr(p2, f"elo_{surf_key}", 1500.0) if p2 else 1500.0
    p_set = round(1.0 / (1.0 + 10 ** ((elo2_surf - elo1_surf) / 400.0)), 4)
    p_set = float(np.clip(p_set, 0.05, 0.95))

    # First-set winner (shrunk toward match prob — sets are noisier than matches)
    p1_first_set = round(float(np.clip(0.5 * p_set + 0.5 * p, 0.05, 0.95)), 4)

    # Expected total games: E[sets] × avg games/set
    # For best-of-3: E[sets] = 2 + 2·p_s·(1-p_s)
    # Surface-specific games/set: Clay ~9.8, Hard ~9.2, Grass ~8.7, Carpet ~9.0
    gpset_map = {"hard": 9.2, "clay": 9.8, "grass": 8.7, "carpet": 9.0}
    avg_gps   = gpset_map.get(surf_key, 9.2)
    exp_sets  = 2 + 2 * p_set * (1 - p_set)
    exp_games = exp_sets * avg_gps
    # O/U calibration: logistic scaling around each line
    def _ou(line: float) -> float:
        raw = 0.5 + (exp_games - line) * 0.08
        return round(float(np.clip(raw, 0.05, 0.95)), 4)

    # Set handicap (best-of-3): P(p1 wins 2-0), P(p1 wins 2-1), etc.
    p1_2_0 = round(p_set ** 2, 4)
    p1_2_1 = round(2 * p_set * (1 - p_set) * p_set, 4)   # lose 1st, win 2nd + 3rd OR win 1st, lose 2nd, win 3rd
    p2_2_0 = round((1 - p_set) ** 2, 4)
    p2_2_1 = round(1.0 - p1_2_0 - p1_2_1 - p2_2_0, 4)

    return {
        "player1":          req.player1,
        "player2":          req.player2,
        "surface":          req.surface,
        "p1_win_prob":      p1_win_prob,
        "p2_win_prob":      p2_win_prob,
        "predicted_winner": req.player1 if p1_win_prob >= 0.5 else req.player2,
        "confidence":       round(max(p1_win_prob, p2_win_prob) * 100, 1),
        "model_used":       "TennisModel (XGB+ELO)" if model else "ELO-only (model not trained)",
        "elo": {
            "p1_overall": round(p1.elo_overall if p1 else 1500.0, 1),
            "p1_surface": round(elo1_surf, 1),
            "p2_overall": round(p2.elo_overall if p2 else 1500.0, 1),
            "p2_surface": round(elo2_surf, 1),
        },
        "markets": {
            "first_set": {
                "p1_win": p1_first_set,
                "p2_win": round(1.0 - p1_first_set, 4),
            },
            "total_games": {
                "expected":   round(exp_games, 1),
                "over_21_5":  _ou(21.5),
                "over_23_5":  _ou(23.5),
                "over_25_5":  _ou(25.5),
                "under_21_5": round(1 - _ou(21.5), 4),
                "under_23_5": round(1 - _ou(23.5), 4),
                "under_25_5": round(1 - _ou(25.5), 4),
            },
            "set_handicap": {
                "p1_2_0": p1_2_0,
                "p1_2_1": p1_2_1,
                "p2_2_0": p2_2_0,
                "p2_2_1": max(0.0, p2_2_1),
            },
        },
    }
