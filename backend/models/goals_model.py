"""
Goals model: predicts Over/Under 2.5 goals
"""
import joblib
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import classification_report, confusion_matrix


FEATURES = [
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    "home_attack_vs_away_defense",
    "away_attack_vs_home_defense",
    "h2h_goals_avg",
    "home_xg_avg",
    "away_xg_avg",
    "home_shot_quality_avg",
    "away_shot_quality_avg",
    "xg_diff",
    "shot_quality_diff",
    # Corners — direct goals-volume proxy
    "home_corners_avg",
    "away_corners_avg",
    "corner_diff",
    # Fatigue & congestion — affects scoring rate
    "home_days_rest",
    "away_days_rest",
    "congestion_diff",
    "home_elo",
    "away_elo",
    "elo_diff",
    "home_ha_score",
    "away_ha_score",
    "season_progress",
    # Opening & closing market lines
    "mkt_open_home_prob",
    "mkt_open_draw_prob",
    "mkt_open_away_prob",
    "mkt_close_home_prob",
    "mkt_close_draw_prob",
    "mkt_close_away_prob",
    "ref_avg_yellow",
    "ref_avg_red",
    # Dixon-Coles expected goals signal
    "dc_home_prob",
    "dc_draw_prob",
    "dc_away_prob",
    "elo_win_prob",
    # Newly promoted sides score / concede more variably
    "home_newly_promoted",
    "away_newly_promoted",
]

TARGET = "over_2_5_goals"
CUTOFF_SEASON = 2023

class GoalsModel:
    def __init__(self):
        self.model = RandomForestClassifier()

    def _cols(self, df: pd.DataFrame) -> list[str]:
        stored = getattr(self, "_trained_features", FEATURES)
        return [f for f in stored if f in df.columns]

    def train(self, df: pd.DataFrame):
        train_df = df[df["season"] < CUTOFF_SEASON].dropna(subset=[TARGET]).copy()
        cols = [f for f in FEATURES if f in train_df.columns]
        X_train = train_df[cols].fillna(0.0)
        y_train = train_df[TARGET]
        self._trained_features = cols

        self.model = CalibratedClassifierCV(
            RandomForestClassifier(n_estimators=60, max_depth=10, random_state=42),
            method="isotonic",
            cv=3,
        )
        self.model.fit(X_train, y_train)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict(df[self._cols(df)].fillna(0.0))

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(df[self._cols(df)].fillna(0.0))

    def evaluate(self, df: pd.DataFrame) -> dict:
        test_df = df[df["season"] >= CUTOFF_SEASON].dropna(subset=[TARGET]).copy()
        if len(test_df) == 0:
            return {"accuracy": 0, "macro_f1": 0, "weighted_f1": 0, "test_samples": 0}
        cols = self._cols(test_df)
        X_test = test_df[cols].fillna(0.0)
        y_test = test_df[TARGET]
        y_pred = self.model.predict(X_test)
        report = classification_report(y_test, y_pred, output_dict=True)
        print(classification_report(y_test, y_pred))
        return {
            "accuracy":     report["accuracy"],
            "macro_f1":     report["macro avg"]["f1-score"],
            "weighted_f1":  report["weighted avg"]["f1-score"],
            "test_samples": len(y_test),
        }

    def save(self, file_path: str):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        joblib.dump(self.model, file_path)
        joblib.dump(getattr(self, "_trained_features", FEATURES),
                    file_path.replace(".pkl", "_features.pkl"))

    def load(self, file_path: str):
        self.model = joblib.load(file_path)
        feat_path = file_path.replace(".pkl", "_features.pkl")
        if os.path.exists(feat_path):
            self._trained_features = joblib.load(feat_path)


# ---------------------------------------------------------------------------
# Multi-line Over/Under model — trains one classifier per goal line
# ---------------------------------------------------------------------------

LINES = [1.5, 2.5, 3.5, 4.5]


class MultiLineGoalsModel:
    """
    Trains and serves independent calibrated classifiers for each O/U line.
    Separate models per line capture different statistical patterns:
    - 1.5 line: dominated by clean-sheet / shutout probabilities
    - 3.5/4.5:  high-scoring match signals (both teams attack, weak defences)
    Dixon-Coles covers these analytically, but the ML model picks up
    non-linear patterns DC misses (e.g. congestion + high line = fewer goals).
    """

    def __init__(self):
        self.models: dict[float, CalibratedClassifierCV] = {}
        self._trained_features: dict[float, list[str]] = {}

    def _target_col(self, line: float) -> str:
        return f"over_{str(line).replace('.', '_')}_goals"

    def _build_target(self, df: pd.DataFrame, line: float) -> pd.Series:
        col = self._target_col(line)
        if col in df.columns:
            return df[col]
        if "home_team_score" in df.columns and "away_team_score" in df.columns:
            total = df["home_team_score"].fillna(0) + df["away_team_score"].fillna(0)
            return (total > line).astype(float)
        return pd.Series(np.nan, index=df.index)

    def train(self, df: pd.DataFrame):
        train_df = df[df["season"] < CUTOFF_SEASON].copy()
        for line in LINES:
            target = self._build_target(train_df, line)
            mask   = target.notna()
            if mask.sum() < 100:
                continue
            cols = [f for f in FEATURES if f in train_df.columns]
            X    = train_df.loc[mask, cols].fillna(0.0)
            y    = target[mask].astype(int)

            m = CalibratedClassifierCV(
                RandomForestClassifier(n_estimators=80, max_depth=10, random_state=42),
                method="isotonic",
                cv=3,
            )
            m.fit(X, y)
            self.models[line]            = m
            self._trained_features[line] = cols
            print(f"    O/U {line}: trained on {mask.sum()} samples")

    def predict_proba(self, df: pd.DataFrame, line: float) -> np.ndarray:
        """Returns (n, 2) array [under_prob, over_prob] for the given line."""
        if line not in self.models:
            return np.full((len(df), 2), 0.5)
        cols = self._trained_features.get(line, FEATURES)
        avail = [f for f in cols if f in df.columns]
        return self.models[line].predict_proba(df[avail].fillna(0.0))

    def all_lines_proba(self, df: pd.DataFrame) -> dict[float, np.ndarray]:
        return {line: self.predict_proba(df, line) for line in LINES if line in self.models}

    def save(self, dir_path: str):
        os.makedirs(dir_path, exist_ok=True)
        for line, m in self.models.items():
            key = str(line).replace(".", "_")
            joblib.dump(m, os.path.join(dir_path, f"goals_ou_{key}.pkl"))
            joblib.dump(self._trained_features.get(line, []),
                        os.path.join(dir_path, f"goals_ou_{key}_features.pkl"))

    def load(self, dir_path: str):
        for line in LINES:
            key  = str(line).replace(".", "_")
            path = os.path.join(dir_path, f"goals_ou_{key}.pkl")
            if os.path.exists(path):
                self.models[line] = joblib.load(path)
                feat_path = path.replace(".pkl", "_features.pkl")
                if os.path.exists(feat_path):
                    self._trained_features[line] = joblib.load(feat_path)
