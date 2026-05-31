"""
Sequence builder for the Bi-LSTM model.

For each match builds two fixed-length tensors representing the recent form
trajectory of the home and away team respectively.

Features per timestep (8 total):
    goals_scored, goals_conceded, xg_for, xg_against,
    result_encoded (H=1, D=0, A=-1),
    is_home (1 if team played at home in that match),
    over_2_5 (0/1), btts (0/1)

Cold-start teams with fewer than `window` prior matches are zero-padded
at the beginning of the sequence (left-pad).
"""

import numpy as np
import pandas as pd
import torch
from collections import defaultdict

SEQ_FEATURES = [
    "goals_scored",
    "goals_conceded",
    "xg_for",
    "xg_against",
    "result_encoded",
    "is_home",
    "over_2_5",
    "btts",
]
N_FEATURES = len(SEQ_FEATURES)
DEFAULT_WINDOW = 5

RESULT_ENC = {"H": 1.0, "D": 0.0, "A": -1.0}


def _f(v, default: float = 0.0) -> float:
    """Safe float: returns default for None / NaN."""
    try:
        f = float(v)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _encode_result_for_team(result: str, team: str, home_team: str) -> float:
    if result is None:
        return 0.0
    if team == home_team:
        return RESULT_ENC.get(result, 0.0)
    else:
        # Away perspective: invert
        return RESULT_ENC.get({"H": "A", "D": "D", "A": "H"}.get(result, "D"), 0.0)


def _build_team_history(df: pd.DataFrame) -> dict[str, list[dict]]:
    """
    Returns a dict: team_name → chronological list of match feature dicts,
    built by scanning finished matches in order.
    """
    history: dict[str, list[dict]] = defaultdict(list)
    df = df.sort_values("match_date")

    for _, row in df.iterrows():
        home = row["home_team"]
        away = row["away_team"]
        result = row.get("result")

        h_scored = _f(row.get("home_team_score"))
        a_scored = _f(row.get("away_team_score"))
        h_xg = _f(row.get("home_team_xG"))
        a_xg = _f(row.get("away_team_xG"))
        over_2_5 = _f(row.get("over_2_5_goals"))
        btts = _f(row.get("btts"))

        history[home].append({
            "goals_scored":   float(h_scored),
            "goals_conceded": float(a_scored),
            "xg_for":         float(h_xg),
            "xg_against":     float(a_xg),
            "result_encoded": _encode_result_for_team(result, home, home),
            "is_home":        1.0,
            "over_2_5":       over_2_5,
            "btts":           btts,
        })
        history[away].append({
            "goals_scored":   float(a_scored),
            "goals_conceded": float(h_scored),
            "xg_for":         float(a_xg),
            "xg_against":     float(h_xg),
            "result_encoded": _encode_result_for_team(result, away, home),
            "is_home":        0.0,
            "over_2_5":       over_2_5,
            "btts":           btts,
        })

    return history


def _team_sequence(
    history: list[dict],
    window: int,
) -> np.ndarray:
    """
    Returns shape (window, N_FEATURES). Left-pads with zeros when history < window.
    Uses only matches BEFORE the current one (caller slices before passing).
    """
    seq = np.zeros((window, N_FEATURES), dtype=np.float32)
    recent = history[-window:] if len(history) >= window else history
    for i, match in enumerate(recent):
        offset = window - len(recent)
        seq[offset + i] = [match[f] for f in SEQ_FEATURES]
    return seq


def build_sequences(
    df: pd.DataFrame,
    window: int = DEFAULT_WINDOW,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Processes df (must include finished matches sorted by match_date) and returns
    three arrays aligned row-by-row with df:

        home_seqs: (n, window, N_FEATURES)
        away_seqs: (n, window, N_FEATURES)
        labels:    (n,) int — 0=H, 1=D, 2=A  (None rows get -1)

    Only rows where result is not None produce valid labels. The arrays cover
    all rows in df; callers filter out label==-1 for training.
    """
    df = df.sort_values("match_date").reset_index(drop=True)
    history: dict[str, list[dict]] = defaultdict(list)

    n = len(df)
    home_seqs = np.zeros((n, window, N_FEATURES), dtype=np.float32)
    away_seqs = np.zeros((n, window, N_FEATURES), dtype=np.float32)
    labels = np.full(n, -1, dtype=np.int64)

    label_map = {"H": 0, "D": 1, "A": 2}

    for idx, row in df.iterrows():
        home = row["home_team"]
        away = row["away_team"]
        result = row.get("result")

        # Build sequences from history *before* this match
        home_seqs[idx] = _team_sequence(history[home], window)
        away_seqs[idx] = _team_sequence(history[away], window)

        if result in label_map:
            labels[idx] = label_map[result]

        # Append current match to history
        h_scored = _f(row.get("home_team_score"))
        a_scored = _f(row.get("away_team_score"))
        h_xg = _f(row.get("home_team_xG"))
        a_xg = _f(row.get("away_team_xG"))
        over_2_5 = _f(row.get("over_2_5_goals"))
        btts = _f(row.get("btts"))

        history[home].append({
            "goals_scored":   float(h_scored),
            "goals_conceded": float(a_scored),
            "xg_for":         float(h_xg),
            "xg_against":     float(a_xg),
            "result_encoded": _encode_result_for_team(result, home, home),
            "is_home":        1.0,
            "over_2_5":       over_2_5,
            "btts":           btts,
        })
        history[away].append({
            "goals_scored":   float(a_scored),
            "goals_conceded": float(h_scored),
            "xg_for":         float(a_xg),
            "xg_against":     float(h_xg),
            "result_encoded": _encode_result_for_team(result, away, home),
            "is_home":        0.0,
            "over_2_5":       over_2_5,
            "btts":           btts,
        })

    return home_seqs, away_seqs, labels


def to_tensors(
    home_seqs: np.ndarray,
    away_seqs: np.ndarray,
    labels: np.ndarray | None = None,
) -> tuple:
    h = torch.from_numpy(home_seqs)
    a = torch.from_numpy(away_seqs)
    if labels is not None:
        return h, a, torch.from_numpy(labels)
    return h, a
