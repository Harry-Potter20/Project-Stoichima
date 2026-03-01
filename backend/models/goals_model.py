"""
Goals model: predicts Over/Under 2.5 goals
"""
import joblib
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix


FEATURES = [
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    "home_attack_vs_away_defense",
    "away_attack_vs_home_defense"
]

TARGET = "over_2_5_goals"
CUTOFF_SEASON = 2023

class GoalsModel:
    def __init__(self):
        self.model = RandomForestClassifier()

    def train(self, df: pd.DataFrame):
        train_df = df[df["season"] < CUTOFF_SEASON]
        X_train = train_df[FEATURES]
        y_train = train_df[TARGET]

        self.model.fit(X_train, y_train)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X_test = df[FEATURES]
        return self.model.predict(X_test)
    
    def evaluate(self, df: pd.DataFrame):
        test_df = df[df["season"] >= CUTOFF_SEASON]
        X_test = test_df[FEATURES]
        y_test = test_df[TARGET]
        y_pred = self.model.predict(X_test)
        print(classification_report(y_test, y_pred))
        print(confusion_matrix(y_test, y_pred))

    def save(self, file_path: str):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        joblib.dump(self.model, file_path)

    def load(self, file_path: str):
        self.model = joblib.load(file_path)
