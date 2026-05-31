"""
Tennis feature engineering.

Computes surface-specific ELO ratings from the full match history, then
builds per-match features used by the TennisModel.

ELO implementation:
  K = 32 for Challengers/lower, 40 for ATP 500/Masters, 50 for Grand Slams
  Starting ELO = 1500
  Surface ELOs are updated independently for Clay / Grass / Hard / Carpet.
  New-player surface ELO inherits 0.5 * overall + 500 (regression to mean).

Features produced (player1 perspective):
  elo_diff, surface_elo_diff           — strength differential
  rank_diff_log                        — log(rank_p2 / rank_p1), handles missing
  p1_form_win_rate, p2_form_win_rate   — last 10 match win rate
  p1_surface_win_rate, p2_surface_win_rate — career surface win rate
  h2h_p1_win_rate                      — head-to-head win rate (all surfaces)
  h2h_surface_p1_win_rate              — H2H on this surface
  p1_serve_efficiency, p2_serve_efficiency — rolling 1stWon/svpt
  p1_return_efficiency, p2_return_efficiency — rolling bp_won/bp_faced
  is_grand_slam, is_masters            — tournament prestige (affects form weight)
"""

import numpy as np
import pandas as pd
from collections import defaultdict
from sqlalchemy.orm import Session


# ── ELO constants ──────────────────────────────────────────────────────────────

K_MAP = {
    "G": 50,   # Grand Slam
    "M": 40,   # Masters / WTA Premier Mandatory
    "A": 32,   # ATP 500 / WTA Premier
    "D": 20,   # Davis Cup / Fed Cup
    "F": 32,   # default
}

SURFACES = ["Clay", "Grass", "Hard", "Carpet"]
FORM_WINDOW = 10
H2H_WINDOW  = 20
SERVE_WINDOW = 8


def _elo_expected(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


def _k_factor(level: str) -> int:
    return K_MAP.get(str(level).upper()[:1], 32)


def _surface_key(surface: str) -> str:
    s = str(surface).strip().capitalize()
    return s if s in SURFACES else "Hard"


def compute_elo_ratings(matches_df: pd.DataFrame) -> dict[str, dict]:
    """
    Compute overall + surface-specific ELO for every player from scratch.

    Args:
        matches_df: DataFrame with columns:
            tourney_date, tourney_level, surface, player1_name, player2_name, winner

    Returns:
        dict: player_name → {overall, Clay, Grass, Hard, Carpet, matches_played}
    """
    df = matches_df.dropna(subset=["player1_name", "player2_name", "winner"]).copy()
    df = df.sort_values("tourney_date")

    elo: dict[str, dict] = defaultdict(lambda: {
        "overall": 1500.0,
        "Clay":    1500.0,
        "Grass":   1500.0,
        "Hard":    1500.0,
        "Carpet":  1500.0,
        "matches_played": 0,
    })

    for _, row in df.iterrows():
        p1   = row["player1_name"]
        p2   = row["player2_name"]
        w    = int(row["winner"])          # 1 → p1 won, 2 → p2 won
        surf = _surface_key(row.get("surface", "Hard"))
        k    = _k_factor(row.get("tourney_level", "F"))

        # ── Overall ELO ──
        e1   = _elo_expected(elo[p1]["overall"], elo[p2]["overall"])
        s1   = 1.0 if w == 1 else 0.0
        elo[p1]["overall"] += k * (s1 - e1)
        elo[p2]["overall"] += k * ((1 - s1) - (1 - e1))

        # ── Surface ELO ──
        es1  = _elo_expected(elo[p1][surf], elo[p2][surf])
        elo[p1][surf] += k * (s1 - es1)
        elo[p2][surf] += k * ((1 - s1) - (1 - es1))

        elo[p1]["matches_played"] += 1
        elo[p2]["matches_played"] += 1

    return dict(elo)


def save_elo_to_db(elo_dict: dict[str, dict], tour: str, db: Session):
    """Upsert computed ELO ratings into the tennis_player_elo table."""
    from app.models import TennisPlayerElo
    from datetime import datetime

    now = datetime.utcnow()
    for player, ratings in elo_dict.items():
        existing = db.query(TennisPlayerElo).filter_by(player_name=player).first()
        if existing:
            existing.elo_overall    = round(ratings["overall"], 2)
            existing.elo_clay       = round(ratings["Clay"], 2)
            existing.elo_grass      = round(ratings["Grass"], 2)
            existing.elo_hard       = round(ratings["Hard"], 2)
            existing.elo_carpet     = round(ratings["Carpet"], 2)
            existing.matches_played = ratings["matches_played"]
            existing.as_of_date     = now
        else:
            db.add(TennisPlayerElo(
                player_name    = player,
                tour           = tour,
                elo_overall    = round(ratings["overall"], 2),
                elo_clay       = round(ratings["Clay"], 2),
                elo_grass      = round(ratings["Grass"], 2),
                elo_hard       = round(ratings["Hard"], 2),
                elo_carpet     = round(ratings["Carpet"], 2),
                matches_played = ratings["matches_played"],
                as_of_date     = now,
            ))
    db.commit()


def _safe_ratio(a, b, default=0.5):
    try:
        return float(a) / float(b) if float(b) > 0 else default
    except (TypeError, ValueError):
        return default


def build_tennis_features(matches_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build training features for the tennis model.
    ALL features are from the perspective of player1 (=winner in training data).
    At inference time, randomly assign home/away to p1/p2 and flip signs for the
    other permutation.

    Returns the matches_df augmented with feature columns + a `target` column (1 = p1 won).
    """
    df = matches_df.dropna(subset=["player1_name", "player2_name", "winner"]).copy()
    df = df.sort_values("tourney_date")

    # ── Per-player running state ──────────────────────────────────────────────
    elo: dict = defaultdict(lambda: {
        "overall": 1500.0, "Clay": 1500.0, "Grass": 1500.0,
        "Hard": 1500.0, "Carpet": 1500.0,
    })
    form:         dict = defaultdict(list)   # list of 0/1 (won)
    surface_form: dict = defaultdict(lambda: defaultdict(list))
    serve_hist:   dict = defaultdict(list)   # list of {svpt, first_won}
    return_hist:  dict = defaultdict(list)   # list of {bp_faced, bp_won}
    h2h:          dict = defaultdict(list)   # (p1, p2) → list of 0/1
    h2h_surface:  dict = defaultdict(lambda: defaultdict(list))

    rows_out = []

    for _, row in df.iterrows():
        p1   = row["player1_name"]
        p2   = row["player2_name"]
        w    = int(row["winner"])
        surf = _surface_key(row.get("surface", "Hard"))
        k    = _k_factor(row.get("tourney_level", "F"))
        key  = tuple(sorted([p1, p2]))

        # ── Feature snapshot BEFORE updating state ────────────────────────────
        elo1_overall = elo[p1]["overall"]
        elo2_overall = elo[p2]["overall"]
        elo1_surf    = elo[p1][surf]
        elo2_surf    = elo[p2][surf]

        r1 = row.get("player1_rank") or 200
        r2 = row.get("player2_rank") or 200

        p1_form  = np.mean(form[p1][-FORM_WINDOW:]) if form[p1] else 0.5
        p2_form  = np.mean(form[p2][-FORM_WINDOW:]) if form[p2] else 0.5
        p1_surf_wr = np.mean(surface_form[p1][surf][-FORM_WINDOW*2:]) if surface_form[p1][surf] else 0.5
        p2_surf_wr = np.mean(surface_form[p2][surf][-FORM_WINDOW*2:]) if surface_form[p2][surf] else 0.5

        h2h_hist    = h2h[key][-H2H_WINDOW:]
        h2h_s_hist  = h2h_surface[key][surf][-H2H_WINDOW:]

        # P1 wins in H2H history (p1 is player1_name in those records)
        p1_h2h_wr   = np.mean([1 if (m[0] == p1) else 0 for m in h2h_hist]) if h2h_hist else 0.5
        p1_h2h_surf = np.mean([1 if (m[0] == p1) else 0 for m in h2h_s_hist]) if h2h_s_hist else 0.5

        # Serve / return efficiencies (rolling)
        p1_serve = np.mean([s["ratio"] for s in serve_hist[p1][-SERVE_WINDOW:]]) if serve_hist[p1] else 0.65
        p2_serve = np.mean([s["ratio"] for s in serve_hist[p2][-SERVE_WINDOW:]]) if serve_hist[p2] else 0.65
        p1_ret   = np.mean([s["ratio"] for s in return_hist[p1][-SERVE_WINDOW:]]) if return_hist[p1] else 0.35
        p2_ret   = np.mean([s["ratio"] for s in return_hist[p2][-SERVE_WINDOW:]]) if return_hist[p2] else 0.35

        is_gs      = 1 if str(row.get("tourney_level", "")).upper() == "G" else 0
        is_masters = 1 if str(row.get("tourney_level", "")).upper() == "M" else 0

        rows_out.append({
            # Target
            "target":                1 if w == 1 else 0,
            # ELO
            "elo_diff":              elo1_overall - elo2_overall,
            "surface_elo_diff":      elo1_surf    - elo2_surf,
            # Ranking
            "rank_diff_log":         np.log(r2 / r1) if r1 > 0 and r2 > 0 else 0.0,
            # Form
            "p1_form_win_rate":      p1_form,
            "p2_form_win_rate":      p2_form,
            "form_diff":             p1_form - p2_form,
            # Surface win rate
            "p1_surface_win_rate":   p1_surf_wr,
            "p2_surface_win_rate":   p2_surf_wr,
            "surface_wr_diff":       p1_surf_wr - p2_surf_wr,
            # H2H
            "h2h_p1_win_rate":       p1_h2h_wr,
            "h2h_surface_p1_wr":     p1_h2h_surf,
            # Serve / return
            "p1_serve_efficiency":   p1_serve,
            "p2_serve_efficiency":   p2_serve,
            "serve_diff":            p1_serve - p2_serve,
            "p1_return_efficiency":  p1_ret,
            "p2_return_efficiency":  p2_ret,
            "return_diff":           p1_ret - p2_ret,
            # Tournament context
            "is_grand_slam":         is_gs,
            "is_masters":            is_masters,
        })

        # ── Update state AFTER recording features ─────────────────────────────
        e1  = _elo_expected(elo1_overall, elo2_overall)
        s1  = 1.0 if w == 1 else 0.0
        elo[p1]["overall"] += k * (s1 - e1)
        elo[p2]["overall"] += k * ((1 - s1) - (1 - e1))
        es1 = _elo_expected(elo1_surf, elo2_surf)
        elo[p1][surf] += k * (s1 - es1)
        elo[p2][surf] += k * ((1 - s1) - (1 - es1))

        winner_p = p1 if w == 1 else p2
        loser_p  = p2 if w == 1 else p1
        form[winner_p].append(1)
        form[loser_p].append(0)
        surface_form[winner_p][surf].append(1)
        surface_form[loser_p][surf].append(0)

        h2h[key].append((winner_p, surf))
        h2h_surface[key][surf].append((winner_p, surf))

        # Serve stats
        for player, svpt_col, won_col in [
            (p1, "p1_svpt", "p1_1st_won"),
            (p2, "p2_svpt", "p2_1st_won"),
        ]:
            svpt = row.get(svpt_col)
            won  = row.get(won_col)
            if svpt and won and float(svpt) > 0:
                serve_hist[player].append({"ratio": float(won) / float(svpt)})

        # Return stats (break points won = bp_faced - bp_saved for opponent)
        for player, faced_col, saved_col in [
            (p1, "p2_bp_faced", "p2_bp_saved"),   # p1 is returner against p2 serve
            (p2, "p1_bp_faced", "p1_bp_saved"),
        ]:
            faced = row.get(faced_col)
            saved = row.get(saved_col)
            if faced and saved is not None and float(faced) > 0:
                bp_won = float(faced) - float(saved)
                return_hist[player].append({"ratio": bp_won / float(faced)})

    feature_df = pd.DataFrame(rows_out, index=df.index)
    return pd.concat([df.reset_index(drop=True), feature_df.reset_index(drop=True)], axis=1)


TENNIS_FEATURES = [
    "elo_diff", "surface_elo_diff",
    "rank_diff_log",
    "p1_form_win_rate", "p2_form_win_rate", "form_diff",
    "p1_surface_win_rate", "p2_surface_win_rate", "surface_wr_diff",
    "h2h_p1_win_rate", "h2h_surface_p1_wr",
    "p1_serve_efficiency", "p2_serve_efficiency", "serve_diff",
    "p1_return_efficiency", "p2_return_efficiency", "return_diff",
    "is_grand_slam", "is_masters",
]
