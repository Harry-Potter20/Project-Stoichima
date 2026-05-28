"""
Match outcome model: predicts Home Win / Draw / Away Win
"""
import joblib
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder
from imblearn.over_sampling import SMOTE


FEATURES = [
    "home_form",
    "away_form",
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    "h2h_home_win_rate",
    "home_xg_avg",
    "away_xg_avg",
]

TARGET = "result"
CUTOFF_SEASON = 2023

class MatchOutcomeModel:
    def __init__(self):
        self.model = XGBClassifier(
            n_estimators=200,
            learning_rate=0.05,
            eval_metric="mlogloss",
            random_state=42
        )
        self.label_encoder = LabelEncoder()

    def train(self, df: pd.DataFrame):
        train_df = df[df["season"] < CUTOFF_SEASON]
        X_train = train_df[FEATURES]
        y_train = train_df[TARGET]
        
        smote = SMOTE(random_state=42)
        X_resampled, y_resampled = smote.fit_resample(X_train, y_train)
        y_resampled = self.label_encoder.fit_transform(y_resampled)
        self.model.fit(X_resampled, y_resampled)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X_test = df[FEATURES]
        return self.model.predict(X_test)

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        X_test = df[FEATURES]
        raw = self.model.predict_proba(X_test)
        # Columns are in label-encoded order; reorder to H, D, A
        classes = list(self.label_encoder.classes_)
        idx = {c: i for i, c in enumerate(classes)}
        order = [idx[c] for c in ["H", "D", "A"]]
        return raw[:, order]
    
    def evaluate(self, df: pd.DataFrame) -> dict:
        test_df = df[df["season"] >= CUTOFF_SEASON]
        X_test = test_df[FEATURES]
        y_test = self.label_encoder.transform(test_df[TARGET])
        y_pred = self.model.predict(X_test)
        report = classification_report(y_test, y_pred, output_dict=True)
        print(classification_report(y_test, y_pred))
        return {
            "accuracy":       report["accuracy"],
            "macro_f1":       report["macro avg"]["f1-score"],
            "weighted_f1":    report["weighted avg"]["f1-score"],
            "test_samples":   len(y_test),
        }
        
    def decode(self, predictions):
        return self.label_encoder.inverse_transform(predictions)

    def save(self, file_path: str):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        joblib.dump(self.model, file_path)
        joblib.dump(self.label_encoder, file_path.replace(".pkl", "_label_encoder.pkl"))

    def load(self, file_path: str):
        self.model = joblib.load(file_path)
        self.label_encoder = joblib.load(file_path.replace(".pkl", "_label_encoder.pkl"))
