"""
PlayerFormModel — XGBoost classifier that adds squad-level player features
on top of the base match features from Phase 1/2.

Kept as a separate model so it can be swapped in/out of the ensemble
independently (useful when player data is sparse for a competition).
"""

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report
from xgboost import XGBClassifier
from imblearn.over_sampling import SMOTE

# Base features inherited from Phase 1+2
BASE_FEATURES = [
    "home_form",
    "away_form",
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    "h2h_home_win_rate",
    "h2h_draw_rate",
    "h2h_goals_avg",
    "home_xg_avg",
    "away_xg_avg",
    "home_shot_quality_avg",
    "away_shot_quality_avg",
    "xg_diff",
    "shot_quality_diff",
    "home_days_rest",
    "away_days_rest",
    "rest_advantage",
    "home_elo",
    "away_elo",
    "elo_diff",
    "home_ha_score",
    "away_ha_score",
    "season_progress",
    "mkt_open_home_prob",
    "mkt_open_draw_prob",
    "mkt_open_away_prob",
    "ref_avg_yellow",
    "ref_avg_red",
    "ref_home_win_rate",
]

# Player-layer features added by player_feature_engineering.build_player_features()
PLAYER_FEATURES = [
    "home_squad_xg_avg",
    "home_squad_xa_avg",
    "away_squad_xg_avg",
    "away_squad_xa_avg",
    "home_availability_score",
    "away_availability_score",
]

FEATURES = BASE_FEATURES + PLAYER_FEATURES
TARGET = "result"
CUTOFF_SEASON = 2023


class PlayerFormModel:
    """
    Usage:
        model = PlayerFormModel()
        model.train(df)             # df must have all FEATURES + TARGET columns
        proba = model.predict_proba(df)  # shape (n, 3) — [H, D, A] probabilities
        model.save("saved_models/player_form_model.pkl")
    """

    def __init__(self):
        self.model: XGBClassifier | None = None
        self.label_encoder = LabelEncoder()

    def train(self, df: pd.DataFrame) -> None:
        df = self._fill_player_defaults(df)
        train_df = df[df["season"] < CUTOFF_SEASON].dropna(subset=FEATURES + [TARGET])

        X = train_df[FEATURES].values
        y = self.label_encoder.fit_transform(train_df[TARGET])

        smote = SMOTE(random_state=42)
        X_res, y_res = smote.fit_resample(X, y)

        self.model = CalibratedClassifierCV(
            XGBClassifier(
                n_estimators=300,
                learning_rate=0.05,
                max_depth=5,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="mlogloss",
                random_state=42,
                n_jobs=-1,
            ),
            method="isotonic",
            cv=5,
        )
        self.model.fit(X_res, y_res)

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Returns probabilities shape (n, 3) ordered [H, D, A]."""
        df = self._fill_player_defaults(df)
        X = df[FEATURES]
        raw = self.model.predict_proba(X)
        classes = list(self.label_encoder.classes_)
        idx = {c: i for i, c in enumerate(classes)}
        order = [idx[c] for c in ["H", "D", "A"]]
        return raw[:, order]

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(df)
        labels = ["H", "D", "A"]
        return np.array([labels[i] for i in proba.argmax(axis=1)])

    def evaluate(self, df: pd.DataFrame) -> dict:
        df = self._fill_player_defaults(df)
        test_df = df[df["season"] >= CUTOFF_SEASON].dropna(subset=FEATURES + [TARGET])
        X_test = test_df[FEATURES]
        y_test = self.label_encoder.transform(test_df[TARGET])
        y_pred = self.model.predict(X_test)
        report = classification_report(y_test, y_pred, output_dict=True)
        return {
            "accuracy":      report["accuracy"],
            "macro_f1":      report["macro avg"]["f1-score"],
            "weighted_f1":   report["weighted avg"]["f1-score"],
            "test_samples":  len(y_test),
        }

    def save(self, path: str) -> None:
        joblib.dump({"model": self.model, "encoder": self.label_encoder}, path)

    def load(self, path: str) -> None:
        obj = joblib.load(path)
        self.model   = obj["model"]
        self.label_encoder = obj["encoder"]

    @staticmethod
    def _fill_player_defaults(df: pd.DataFrame) -> pd.DataFrame:
        """Fill missing player columns with neutral defaults so the model still runs
        when player data hasn't been scraped yet for some matches."""
        df = df.copy()
        for col in ["home_squad_xg_avg", "home_squad_xa_avg",
                    "away_squad_xg_avg", "away_squad_xa_avg"]:
            if col not in df.columns:
                df[col] = 0.0
        for col in ["home_availability_score", "away_availability_score"]:
            if col not in df.columns:
                df[col] = 1.0
        # Also fill shot quality defaults for backward compat
        for col in ["home_shot_quality_avg", "away_shot_quality_avg",
                    "xg_diff", "shot_quality_diff"]:
            if col not in df.columns:
                df[col] = 0.0
        return df
