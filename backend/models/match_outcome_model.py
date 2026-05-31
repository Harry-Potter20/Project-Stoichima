"""
Match outcome model: predicts Home Win / Draw / Away Win
"""
import joblib
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, f1_score, accuracy_score
from sklearn.preprocessing import LabelEncoder
from imblearn.over_sampling import SMOTE


FEATURES = [
    # Core form
    "home_form",
    "away_form",
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    # Streaks & draw tendency
    "home_win_streak",
    "away_win_streak",
    "streak_diff",
    "home_unbeaten",
    "away_unbeaten",
    "home_draw_rate",
    "away_draw_rate",
    "combined_draw_rate",
    # H2H
    "h2h_home_win_rate",
    "h2h_draw_rate",
    "h2h_goals_avg",
    # xG & shot quality
    "home_xg_avg",
    "away_xg_avg",
    "home_shot_quality_avg",
    "away_shot_quality_avg",
    "xg_diff",
    "shot_quality_diff",
    # Corners
    "home_corners_avg",
    "away_corners_avg",
    "corner_diff",
    # Fatigue & congestion
    "home_days_rest",
    "away_days_rest",
    "rest_advantage",
    "congestion_diff",
    # ELO strength
    "home_elo",
    "away_elo",
    "elo_diff",
    "elo_win_prob",
    # Home advantage coefficient (per-team stadium fortress effect)
    "home_ha_score",
    "away_ha_score",
    # Table position & pressure (domestic only; NaN → XGBoost default branch)
    "home_table_pos",
    "away_table_pos",
    "table_pos_diff",
    "home_pts_gap_top4",
    "away_pts_gap_top4",
    "home_pts_gap_rel",
    "away_pts_gap_rel",
    # Season context
    "season_progress",
    # Opening market line (first-available sharp signal)
    "mkt_open_home_prob",
    "mkt_open_draw_prob",
    "mkt_open_away_prob",
    # Closing market line (sharpest signal — all smart money settled)
    "mkt_close_home_prob",
    "mkt_close_draw_prob",
    "mkt_close_away_prob",
    # Line movement: closing / opening ratio
    "home_odds_move",
    "draw_odds_move",
    "away_odds_move",
    # Referee tendency
    "ref_avg_yellow",
    "ref_avg_red",
    "ref_home_win_rate",
    # Dixon-Coles principled H/D/A signal
    "dc_home_prob",
    "dc_draw_prob",
    "dc_away_prob",
    # Derived matchup differentials
    "home_away_form_diff",
    "home_attack_vs_away_defense",
    "away_attack_vs_home_defense",
    # Managerial change recency (new manager bounce ~0-30 days)
    "home_days_since_mgr_change",
    "away_days_since_mgr_change",
    # Newly promoted flag — rolling form from lower division is misleading
    "home_newly_promoted",
    "away_newly_promoted",
    # Match importance — title race / relegation battle change tactical approach
    "home_title_pressure",
    "away_title_pressure",
    "home_relegation_danger",
    "away_relegation_danger",
    "match_importance",
    # Sharp-money steam flags
    "home_steam",
    "away_steam",
    "max_steam",
]

TARGET = "result"
CUTOFF_SEASON = 2023
DRAW_FLOOR = 0.18   # never output < 18% draw probability — corrects for SMOTE draw collapse

class MatchOutcomeModel:
    def __init__(self):
        self.model = None   # replaced by CalibratedClassifierCV in train()
        self.label_encoder = LabelEncoder()

    def train(
        self,
        df: pd.DataFrame,
        sample_weight: np.ndarray | None = None,
        xgb_params: dict | None = None,
        feature_override: list[str] | None = None,
    ):
        """
        Train on all rows that have a known result.
        sample_weight:    optional per-row weights (time-decay / CLV-derived).
        xgb_params:       override XGBoost hyperparameters (from Optuna tuning).
        feature_override: use a specific feature subset instead of FEATURES
                          (e.g. after pruning low-gain features).
        NaN features filled with 0 so SMOTE can run; XGBoost handles NaN clusters.
        """
        valid = df.dropna(subset=[TARGET]).sort_values("match_date").copy()

        if sample_weight is not None:
            sw_series = pd.Series(sample_weight, index=df.index)
            sw_series = sw_series.reindex(valid.index).fillna(1.0)
        else:
            sw_series = pd.Series(np.ones(len(valid)), index=valid.index)

        feat_list = feature_override if feature_override else FEATURES
        available = [f for f in feat_list if f in valid.columns]
        X_train = valid[available].fillna(0.0)
        y_train = self.label_encoder.fit_transform(valid[TARGET])

        smote = SMOTE(random_state=42)
        X_resampled, y_resampled = smote.fit_resample(X_train, y_train)

        base_params = dict(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.85,
            colsample_bytree=0.85,
            eval_metric="mlogloss",
            random_state=42,
        )
        if xgb_params:
            base_params.update(xgb_params)

        self.model = CalibratedClassifierCV(
            XGBClassifier(**base_params),
            method="isotonic",
            cv=5,
        )
        self.model.fit(X_resampled, y_resampled)
        self._trained_features = available

    def _feature_matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the feature matrix using only features present at train time."""
        cols = getattr(self, "_trained_features", FEATURES)
        available = [f for f in cols if f in df.columns]
        return df[available].fillna(0.0)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict(self._feature_matrix(df))

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(self._feature_matrix(df))
        # Columns are in label-encoded order; reorder to H, D, A
        classes = list(self.label_encoder.classes_)
        idx = {c: i for i, c in enumerate(classes)}
        order = [idx[c] for c in ["H", "D", "A"]]
        result = raw[:, order].copy()

        # Apply draw floor — SMOTE tends to collapse draw probability toward 0.
        # Lift any row below DRAW_FLOOR by redistributing the deficit from H and A
        # proportionally to their current probabilities.
        deficit = np.maximum(0.0, DRAW_FLOOR - result[:, 1])
        ha_sum  = result[:, 0] + result[:, 2]
        mask    = (deficit > 0) & (ha_sum > 1e-9)
        result[mask, 0] -= deficit[mask] * result[mask, 0] / ha_sum[mask]
        result[mask, 2] -= deficit[mask] * result[mask, 2] / ha_sum[mask]
        result[mask, 1]  = DRAW_FLOOR
        result = np.clip(result, 0.0, 1.0)

        return result

    def explain(self, df: pd.DataFrame) -> list[list[dict]]:
        """
        Returns top-5 SHAP features per row for the predicted class.
        Each row: [{feature, raw_value, shap}]  — sorted by |shap| descending.
        Falls back to empty lists if SHAP is unavailable or model format unexpected.
        """
        try:
            import shap

            X = self._feature_matrix(df)
            X_np = X.values.astype(float)

            # Predicted class index in H/D/A order (0=H, 1=D, 2=A)
            proba = self.predict_proba(df)
            pred_hda = np.argmax(proba, axis=1)

            # label_encoder.classes_ is alphabetical: [A=0, D=1, H=2]
            # Map H/D/A prediction indices back to XGBoost class indices
            hda_to_xgb = {0: 2, 1: 1, 2: 0}

            # Use first base estimator from CalibratedClassifierCV
            base = self.model.calibrated_classifiers_[0].estimator
            explainer = shap.TreeExplainer(base)
            raw_sv = explainer.shap_values(X_np)

            # Normalise to (n_samples, n_features, n_classes)
            if isinstance(raw_sv, list):
                sv = np.stack(raw_sv, axis=-1)
            elif isinstance(raw_sv, np.ndarray) and raw_sv.ndim == 3:
                sv = raw_sv
            else:
                return [[] for _ in range(len(df))]

            feat_names = list(X.columns)
            results = []
            for i in range(len(X)):
                class_idx = hda_to_xgb[int(pred_hda[i])]
                sv_row = sv[i, :, class_idx]
                top_idx = np.argsort(np.abs(sv_row))[::-1][:5]
                results.append([
                    {
                        "feature":   feat_names[j],
                        "raw_value": round(float(X.iloc[i, j]), 3),
                        "shap":      round(float(sv_row[j]), 4),
                    }
                    for j in top_idx
                ])
            return results
        except Exception:
            return [[] for _ in range(len(df))]
    
    def evaluate(self, df: pd.DataFrame, n_splits: int = 5) -> dict:
        """
        Walk-forward expanding-window cross-validation using TimeSeriesSplit.
        Trains on folds 1..k, tests on fold k+1 — no look-ahead leakage.
        Averages accuracy and macro-F1 across all test folds.
        """
        valid = df.dropna(subset=[TARGET]).sort_values("match_date").copy()
        if len(valid) < n_splits * 2:
            # Fallback for very small datasets: simple last-20% holdout
            split = int(len(valid) * 0.8)
            test_df = valid.iloc[split:]
            if len(test_df) == 0:
                return {"accuracy": 0, "macro_f1": 0, "weighted_f1": 0, "test_samples": 0}
            X_test = self._feature_matrix(test_df)
            y_test = self.label_encoder.transform(test_df[TARGET])
            y_pred = self.model.predict(X_test)
            return {
                "accuracy":     round(float(accuracy_score(y_test, y_pred)), 4),
                "macro_f1":     round(float(f1_score(y_test, y_pred, average="macro")), 4),
                "weighted_f1":  round(float(f1_score(y_test, y_pred, average="weighted")), 4),
                "test_samples": len(y_test),
            }

        cols = [f for f in FEATURES if f in valid.columns]
        X_all = valid[cols].fillna(0.0).values
        y_all = self.label_encoder.transform(valid[TARGET])

        tscv = TimeSeriesSplit(n_splits=n_splits)
        accs, f1s, wf1s = [], [], []
        for train_idx, test_idx in tscv.split(X_all):
            if len(train_idx) < 50:   # skip folds with too little training data
                continue
            X_tr, X_te = X_all[train_idx], X_all[test_idx]
            y_tr, y_te = y_all[train_idx], y_all[test_idx]
            # Fit a lightweight clone for evaluation only (no SMOTE/calibration overhead)
            _m = XGBClassifier(n_estimators=100, learning_rate=0.1, max_depth=4,
                               eval_metric="mlogloss", random_state=42)
            _m.fit(X_tr, y_tr)
            y_pred = _m.predict(X_te)
            accs.append(accuracy_score(y_te, y_pred))
            f1s.append(f1_score(y_te, y_pred, average="macro", zero_division=0))
            wf1s.append(f1_score(y_te, y_pred, average="weighted", zero_division=0))

        if not accs:
            return {"accuracy": 0, "macro_f1": 0, "weighted_f1": 0, "test_samples": 0}

        print(f"  Walk-forward CV ({len(accs)} folds): "
              f"acc={np.mean(accs):.3f}±{np.std(accs):.3f}  "
              f"macro_f1={np.mean(f1s):.3f}±{np.std(f1s):.3f}")
        return {
            "accuracy":     round(float(np.mean(accs)), 4),
            "macro_f1":     round(float(np.mean(f1s)), 4),
            "weighted_f1":  round(float(np.mean(wf1s)), 4),
            "test_samples": sum(len(te) for _, te in tscv.split(X_all)),
        }
        
    def decode(self, predictions):
        return self.label_encoder.inverse_transform(predictions)

    @staticmethod
    def tune_hyperparams(df: pd.DataFrame, n_trials: int = 50, timeout: int = 300) -> dict:
        """
        Bayesian hyperparameter search using Optuna (if available).
        Optimises multi-class log-loss on a 5-fold walk-forward CV.
        Returns the best params dict — pass to train() via XGBClassifier(**params).
        Falls back to hardcoded defaults if Optuna is not installed.
        """
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            print("  Optuna not installed — using default hyperparams. "
                  "Run: pip install optuna")
            return {}

        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.preprocessing import LabelEncoder
        from sklearn.metrics import log_loss

        valid = df.dropna(subset=[TARGET]).sort_values("match_date").copy()
        cols  = [f for f in FEATURES if f in valid.columns]
        X_all = valid[cols].fillna(0.0).values
        le    = LabelEncoder()
        y_all = le.fit_transform(valid[TARGET])

        def objective(trial):
            params = {
                "n_estimators":    trial.suggest_int("n_estimators", 100, 600),
                "max_depth":       trial.suggest_int("max_depth", 3, 8),
                "learning_rate":   trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "subsample":       trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree":trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "min_child_weight":trial.suggest_int("min_child_weight", 1, 10),
                "gamma":           trial.suggest_float("gamma", 0.0, 1.0),
                "reg_alpha":       trial.suggest_float("reg_alpha", 0.0, 1.0),
                "reg_lambda":      trial.suggest_float("reg_lambda", 0.5, 5.0),
                "eval_metric": "mlogloss",
                "random_state": 42,
                "use_label_encoder": False,
            }
            tscv   = TimeSeriesSplit(n_splits=5)
            losses = []
            for tr, te in tscv.split(X_all):
                if len(tr) < 50:
                    continue
                m = XGBClassifier(**params)
                m.fit(X_all[tr], y_all[tr])
                proba = m.predict_proba(X_all[te])
                losses.append(log_loss(y_all[te], proba))
            return float(np.mean(losses)) if losses else 99.0

        study = optuna.create_study(direction="minimize")
        study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=False)
        best = study.best_params
        best.pop("use_label_encoder", None)
        print(f"  Optuna best log-loss: {study.best_value:.4f}  params: {best}")
        return best

    def get_feature_importances(self) -> list[dict]:
        """
        Returns features ranked by XGBoost gain importance.
        Uses the first calibrated estimator's base XGBClassifier.
        """
        try:
            base = self.model.calibrated_classifiers_[0].estimator
            cols = getattr(self, "_trained_features", FEATURES)
            imp  = base.feature_importances_
            ranked = sorted(
                [{"feature": c, "importance": round(float(v), 6)}
                 for c, v in zip(cols, imp)],
                key=lambda x: -x["importance"]
            )
            return ranked
        except Exception:
            return []

    def prune_low_importance_features(self, df: pd.DataFrame, min_gain: float = 0.001) -> list[str]:
        """
        Returns a pruned feature list — drops features whose XGBoost gain < min_gain.
        Call after train(); use the returned list to retrain on a cleaner feature set.
        """
        imps = self.get_feature_importances()
        kept = [d["feature"] for d in imps if d["importance"] >= min_gain]
        dropped = [d["feature"] for d in imps if d["importance"] < min_gain]
        if dropped:
            print(f"  Pruning {len(dropped)} low-gain features: {dropped[:8]}{'…' if len(dropped)>8 else ''}")
        return kept

    def save(self, file_path: str):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        joblib.dump(self.model, file_path)
        joblib.dump(self.label_encoder, file_path.replace(".pkl", "_label_encoder.pkl"))
        # Persist the feature list alongside the model
        joblib.dump(
            getattr(self, "_trained_features", FEATURES),
            file_path.replace(".pkl", "_features.pkl"),
        )

    def load(self, file_path: str):
        self.model = joblib.load(file_path)
        self.label_encoder = joblib.load(file_path.replace(".pkl", "_label_encoder.pkl"))
        feat_path = file_path.replace(".pkl", "_features.pkl")
        if os.path.exists(feat_path):
            self._trained_features = joblib.load(feat_path)
        else:
            # Derive feature list from the fitted XGBoost estimator itself so we
            # never pass columns the model wasn't trained on (avoids "unseen at fit" errors).
            try:
                base = self.model.calibrated_classifiers_[0].estimator
                if hasattr(base, "feature_names_in_"):
                    self._trained_features = list(base.feature_names_in_)
            except Exception:
                pass
