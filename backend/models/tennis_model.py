"""
TennisModel — XGBoost binary classifier predicting P(player1 wins).

Features are symmetric: all features are expressed as player1 − player2 differences
or player1-specific stats, so the model is direction-agnostic.

At inference time, randomly (or by seed/rank) assign p1/p2 and the model returns
P(p1 wins). Swap p1↔p2 and the model returns the complement.
"""

import os
import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from xgboost import XGBClassifier

from data_processing.tennis_feature_engineering import TENNIS_FEATURES

CUTOFF_FRAC = 0.80   # walk-forward: train on first 80% by date


_DIFF_COLS  = ["elo_diff", "surface_elo_diff", "rank_diff_log",
               "form_diff", "surface_wr_diff", "serve_diff", "return_diff"]
_SWAP_PAIRS = [
    ("p1_form_win_rate",    "p2_form_win_rate"),
    ("p1_surface_win_rate", "p2_surface_win_rate"),
    ("p1_serve_efficiency", "p2_serve_efficiency"),
    ("p1_return_efficiency","p2_return_efficiency"),
]
_FLIP_COLS  = ["h2h_p1_win_rate", "h2h_surface_p1_wr"]  # mirror = 1 - x


def _mirror_df(df: pd.DataFrame) -> pd.DataFrame:
    """Create losing-player perspective rows (target=0) from all-winner rows."""
    m = df.copy()
    for col in _DIFF_COLS:
        if col in m.columns:
            m[col] = -m[col]
    for a, b in _SWAP_PAIRS:
        if a in m.columns and b in m.columns:
            m[a], m[b] = df[b].values.copy(), df[a].values.copy()
    for col in _FLIP_COLS:
        if col in m.columns:
            m[col] = 1.0 - m[col]
    m["target"] = 0
    return m


class TennisModel:
    def __init__(self):
        self.model = None

    def train(self, feature_df: pd.DataFrame):
        """
        feature_df must contain TENNIS_FEATURES columns and a `target` column (1 = p1 won).
        Rows are expected sorted by tourney_date (already guaranteed by build_tennis_features).
        Augments with mirrored rows so both classes are represented.
        """
        valid = feature_df.dropna(subset=TENNIS_FEATURES + ["target"]).copy()
        # Temporal split on original, then augment each half independently
        split = int(len(valid) * CUTOFF_FRAC)
        train_orig = valid.iloc[:split]
        train_df = pd.concat([train_orig, _mirror_df(train_orig)], ignore_index=True)

        X_train = train_df[TENNIS_FEATURES]
        y_train = train_df["target"].astype(int).values

        base = XGBClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
        )
        self.model = CalibratedClassifierCV(base, method="isotonic", cv=5)
        self.model.fit(X_train, y_train)

    def predict_proba_p1(self, df: pd.DataFrame) -> np.ndarray:
        """Returns P(player1 wins) for each row — shape (n,)."""
        X = df[TENNIS_FEATURES].fillna(0)
        return self.model.predict_proba(X)[:, 1]

    def evaluate(self, feature_df: pd.DataFrame) -> dict:
        valid = feature_df.dropna(subset=TENNIS_FEATURES + ["target"]).copy()
        split = int(len(valid) * CUTOFF_FRAC)
        test_orig = valid.iloc[split:]
        test_df = pd.concat([test_orig, _mirror_df(test_orig)], ignore_index=True)
        if len(test_df) == 0:
            return {}
        X_test = test_df[TENNIS_FEATURES].fillna(0)
        y_true = test_df["target"].astype(int).values
        proba  = self.model.predict_proba(X_test)[:, 1]
        y_pred = (proba >= 0.5).astype(int)
        acc    = (y_pred == y_true).mean()
        auc    = roc_auc_score(y_true, proba)
        brier  = brier_score_loss(y_true, proba)
        ll     = log_loss(y_true, proba)
        return {
            "accuracy": round(float(acc), 4),
            "auc":      round(float(auc), 4),
            "brier":    round(float(brier), 4),
            "log_loss": round(float(ll), 4),
            "test_n":   len(test_df),
        }

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self.model, path)

    def load(self, path: str):
        self.model = joblib.load(path)
