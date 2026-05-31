"""
Own xG model — XGBoost binary classifier trained on Understat shot-level data.

Features derived from raw shot coordinates and metadata; no Understat xG values
used during training (they are kept only for post-hoc validation).

Pitch coordinate convention (Understat):
    x = 0 (own goal line) → 1 (opponent goal line)
    y = 0 (left touchline) → 1 (right touchline)
    Goal centre is approximately at (1.0, 0.5).
"""

import math
import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

GOAL_X = 1.0
GOAL_Y = 0.5

LAST_ACTION_ENCODER = LabelEncoder()

FEATURES = [
    "x",
    "y",
    "distance_to_goal",
    "angle_to_goal",
    "is_header",
    "is_penalty",
    "is_set_piece",
    "is_open_play",
    "is_corner",
    "last_action_encoded",
]


def _derive_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add geometry and boolean features in-place. Returns a new DataFrame."""
    df = df.copy()

    dx = GOAL_X - df["x"]
    dy = GOAL_Y - df["y"]
    df["distance_to_goal"] = np.sqrt(dx**2 + dy**2)

    # Half-angle of the goal mouth visible from the shot position.
    # Goal width ≈ 0.074 in Understat's normalised scale (7.32 m / 100 m pitch).
    goal_half_width = 0.037
    with np.errstate(divide="ignore", invalid="ignore"):
        df["angle_to_goal"] = np.where(
            df["distance_to_goal"] > 0,
            np.arctan2(goal_half_width, df["distance_to_goal"]),
            math.pi / 2,
        )

    df["is_header"]   = (df["shot_type"].str.lower() == "head").astype(int)
    df["is_penalty"]  = (df["situation"].str.lower() == "penalty").astype(int)
    df["is_set_piece"] = df["situation"].str.lower().isin(
        ["setpiece", "set_piece", "fromcorner", "from_corner", "directfreekick", "direct_freekick"]
    ).astype(int)
    df["is_open_play"] = (df["situation"].str.lower() == "openplay").astype(int)
    df["is_corner"]    = df["situation"].str.lower().isin(
        ["fromcorner", "from_corner"]
    ).astype(int)

    return df


class XGModel:
    """
    Binary classifier: 1 = Goal, 0 = No Goal.

    Usage:
        model = XGModel()
        model.train(shots_df)          # shots_df from the shots table
        proba = model.predict_proba(shots_df)  # shape (n,) — P(goal)
        model.save("saved_models/xg_model.pkl")

        model2 = XGModel()
        model2.load("saved_models/xg_model.pkl")
    """

    def __init__(self):
        self.model: XGBClassifier | None = None
        self.last_action_encoder = LabelEncoder()

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, df: pd.DataFrame) -> None:
        df = self._prepare(df, fit_encoder=True)
        X = df[FEATURES]
        y = (df["result"].str.lower() == "goal").astype(int)

        self.model = XGBClassifier(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X, y)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Returns P(goal) for each shot, shape (n,)."""
        df = self._prepare(df, fit_encoder=False)
        X = df[FEATURES]
        return self.model.predict_proba(X)[:, 1]

    def predict_xg(self, df: pd.DataFrame) -> pd.Series:
        """Alias — returns P(goal) as a named Series."""
        proba = self.predict_proba(df)
        return pd.Series(proba, index=df.index, name="xg")

    # ------------------------------------------------------------------
    # Validation helper
    # ------------------------------------------------------------------

    def validate_vs_understat(self, df: pd.DataFrame) -> dict:
        """
        Compare own xG predictions against Understat's xG values.
        Requires `understat_xg` column in df. Returns Brier scores for both.
        """
        from sklearn.metrics import brier_score_loss
        df = df.dropna(subset=["understat_xg", "result"])
        y_true = (df["result"].str.lower() == "goal").astype(int)
        own_xg = self.predict_proba(df)
        brier_own = brier_score_loss(y_true, own_xg)
        brier_understat = brier_score_loss(y_true, df["understat_xg"].values)
        return {
            "brier_own":       round(brier_own, 4),
            "brier_understat": round(brier_understat, 4),
            "n_shots":         len(df),
            "goal_rate":       round(y_true.mean(), 4),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        joblib.dump({"model": self.model, "encoder": self.last_action_encoder}, path)

    def load(self, path: str) -> None:
        obj = joblib.load(path)
        self.model = obj["model"]
        self.last_action_encoder = obj["encoder"]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _prepare(self, df: pd.DataFrame, fit_encoder: bool) -> pd.DataFrame:
        df = _derive_features(df)

        # Encode last_action categorically
        col = df["last_action"].fillna("Unknown").astype(str)
        if fit_encoder:
            self.last_action_encoder.fit(col)
        # Unknown categories during inference → map to first class (index 0)
        known = set(self.last_action_encoder.classes_)
        col = col.where(col.isin(known), other=self.last_action_encoder.classes_[0])
        df["last_action_encoded"] = self.last_action_encoder.transform(col)

        return df
