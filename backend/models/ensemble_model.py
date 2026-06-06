"""
Soft-voting ensemble for match outcome prediction (H / D / A).

Combines probability outputs from:
    1. MatchOutcomeModel   (XGBoost, tabular features)
    2. BiLSTMModel         (PyTorch, sequence features)
    3. PlayerFormModel     (XGBoost, tabular + squad features)

Default strategy: weighted average of predict_proba outputs.
Weights are tunable and stored alongside the model — retrain is not required
to adjust them, just update the saved weights file.

Optional stacking (flag: use_stacking=True): trains a LogisticRegression
meta-learner on out-of-fold predictions from the three base models.
"""

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

DEFAULT_WEIGHTS = {
    "outcome":     0.35,
    "bilstm":      0.40,
    "player_form": 0.25,
}

# Per-competition tuned weights based on walk-forward validation results.
# BiLSTM weight is higher for leagues with volatile form (e.g. Ligue 1).
# outcome_model gets more weight in tactically consistent leagues (Serie A, Bundesliga).
# These are empirically derived starting points — retrain to update.
COMPETITION_WEIGHTS: dict[str, dict] = {
    "PL":  {"outcome": 0.35, "bilstm": 0.40, "player_form": 0.25},
    "PD":  {"outcome": 0.40, "bilstm": 0.35, "player_form": 0.25},  # La Liga: more predictable
    "BL1": {"outcome": 0.42, "bilstm": 0.33, "player_form": 0.25},  # Bundesliga: high home adv
    "SA":  {"outcome": 0.43, "bilstm": 0.32, "player_form": 0.25},  # Serie A: defensive, stable
    "FL1": {"outcome": 0.30, "bilstm": 0.45, "player_form": 0.25},  # Ligue 1: volatile
    "ELC": {"outcome": 0.40, "bilstm": 0.38, "player_form": 0.22},
    "ERE": {"outcome": 0.38, "bilstm": 0.40, "player_form": 0.22},  # Eredivisie: attacking
    "JPL": {"outcome": 0.38, "bilstm": 0.38, "player_form": 0.24},
    "PPL": {"outcome": 0.40, "bilstm": 0.37, "player_form": 0.23},
    "TSL": {"outcome": 0.35, "bilstm": 0.42, "player_form": 0.23},  # Turkish SL: volatile
    "GSL": {"outcome": 0.35, "bilstm": 0.42, "player_form": 0.23},

    # ── International tournaments ──────────────────────────────────────────
    # BiLSTM down-weighted: national teams play sparsely and 4-year cycles mean
    # sequence context is stale. Player form up-weighted: a Mbappé/Messi-level
    # availability decision swings probabilities dramatically.
    "WC":   {"outcome": 0.50, "bilstm": 0.15, "player_form": 0.35},
    "EC":   {"outcome": 0.50, "bilstm": 0.15, "player_form": 0.35},
    "UNL":  {"outcome": 0.50, "bilstm": 0.20, "player_form": 0.30},  # more matches than WC
    "AFC":  {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "ASIA": {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "CA":   {"outcome": 0.50, "bilstm": 0.15, "player_form": 0.35},
    "GOLD": {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},

    # Friendlies — minimal context, model relies almost entirely on the base
    # outcome model + DC. Player form less informative since lineups often
    # rotate heavily in friendlies.
    "FRIENDLY": {"outcome": 0.65, "bilstm": 0.10, "player_form": 0.25},
    "WCQ_EU":   {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "WCQ_AF":   {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "WCQ_AS":   {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "WCQ_SA":   {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
    "WCQ_CC":   {"outcome": 0.55, "bilstm": 0.15, "player_form": 0.30},
}


class EnsembleModel:
    """
    Usage (soft voting):
        ens = EnsembleModel(outcome_model, bilstm_model, player_form_model)
        proba = ens.predict_proba(df)   # (n, 3) — [H, D, A]
        preds = ens.predict(df)         # (n,) — str labels

    Usage (stacking):
        ens = EnsembleModel(..., use_stacking=True)
        ens.fit_stacking(train_df)      # trains meta-learner
        proba = ens.predict_proba(df)
    """

    def __init__(
        self,
        outcome_model,
        bilstm_model,
        player_form_model,
        weights: dict | None = None,
        use_stacking: bool = False,
        competition: str | None = None,
    ):
        self.outcome_model     = outcome_model
        self.bilstm_model      = bilstm_model
        self.player_form_model = player_form_model
        # Use per-competition weights if available, then caller-supplied, then default
        if competition and competition in COMPETITION_WEIGHTS:
            self.weights = COMPETITION_WEIGHTS[competition].copy()
        else:
            self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.competition = competition
        self.use_stacking = use_stacking
        self.meta_learner: LogisticRegression | None = None

    def for_competition(self, competition: str) -> "EnsembleModel":
        """Return a shallow copy of this ensemble with weights tuned for `competition`."""
        clone = EnsembleModel(
            self.outcome_model,
            self.bilstm_model,
            self.player_form_model,
            use_stacking=self.use_stacking,
            competition=competition,
        )
        clone.meta_learner = self.meta_learner
        return clone

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Returns shape (n, 3) [H, D, A] probabilities."""
        if self.use_stacking and self.meta_learner is not None:
            return self._stacking_proba(df)
        return self._soft_vote(df)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(df)
        labels = ["H", "D", "A"]
        return np.array([labels[i] for i in proba.argmax(axis=1)])

    def predict_with_confidence(self, df: pd.DataFrame) -> dict:
        """
        Returns a dict with predictions and component model probabilities for
        transparency / debugging. Useful for the API response.
        """
        outcome_p     = self._safe_proba(self.outcome_model, df)
        bilstm_p      = self._safe_proba(self.bilstm_model, df)
        player_form_p = self._safe_proba(self.player_form_model, df)
        ensemble_p    = self.predict_proba(df)

        return {
            "ensemble":     ensemble_p,
            "outcome":      outcome_p,
            "bilstm":       bilstm_p,
            "player_form":  player_form_p,
        }

    # ------------------------------------------------------------------
    # Stacking
    # ------------------------------------------------------------------

    def fit_stacking(self, df: pd.DataFrame, n_splits: int = 5) -> None:
        """
        Train a LogisticRegression meta-learner using out-of-fold predictions
        from the three base models. Call after all base models are trained.
        """
        label_map = {"H": 0, "D": 1, "A": 2}
        valid = df["result"].isin(label_map)
        df_valid = df[valid].copy()
        y = df_valid["result"].map(label_map).values

        oof_outcome = np.zeros((len(df_valid), 3))
        oof_bilstm  = np.zeros((len(df_valid), 3))
        oof_player  = np.zeros((len(df_valid), 3))

        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        for fold, (train_idx, val_idx) in enumerate(skf.split(df_valid, y)):
            fold_train = df_valid.iloc[train_idx]
            fold_val   = df_valid.iloc[val_idx]

            # We use full predict_proba (models are pre-trained) — this is
            # pseudo-OOF since we can't retrain per fold without heavy cost.
            oof_outcome[val_idx] = self._safe_proba(self.outcome_model, fold_val)
            oof_bilstm[val_idx]  = self._safe_proba(self.bilstm_model, fold_val)
            oof_player[val_idx]  = self._safe_proba(self.player_form_model, fold_val)

        meta_X = np.hstack([oof_outcome, oof_bilstm, oof_player])
        meta_X = np.nan_to_num(meta_X, nan=1.0 / 3.0)  # cold-start fallback: uniform prior
        self.meta_learner = LogisticRegression(max_iter=500, C=0.5, random_state=42)
        self.meta_learner.fit(meta_X, y)

    # ------------------------------------------------------------------
    # Weights
    # ------------------------------------------------------------------

    def set_weights(self, outcome: float, bilstm: float, player_form: float) -> None:
        total = outcome + bilstm + player_form
        self.weights = {
            "outcome":     outcome / total,
            "bilstm":      bilstm / total,
            "player_form": player_form / total,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_weights(self, path: str) -> None:
        """Save only the ensemble config (weights + optional meta-learner).
        Base models save themselves separately."""
        joblib.dump({
            "weights":      self.weights,
            "use_stacking": self.use_stacking,
            "meta_learner": self.meta_learner,
        }, path)

    def load_weights(self, path: str) -> None:
        obj = joblib.load(path)
        self.weights      = obj["weights"]
        self.use_stacking = obj["use_stacking"]
        self.meta_learner = obj.get("meta_learner")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _soft_vote(self, df: pd.DataFrame) -> np.ndarray:
        w = self.weights
        p_outcome     = self._safe_proba(self.outcome_model, df)
        p_bilstm      = self._safe_proba(self.bilstm_model, df)
        p_player_form = self._safe_proba(self.player_form_model, df)
        combined = (
            w["outcome"]     * p_outcome +
            w["bilstm"]      * p_bilstm +
            w["player_form"] * p_player_form
        )
        row_sums = combined.sum(axis=1, keepdims=True)
        return combined / np.where(row_sums > 0, row_sums, 1.0)

    def _stacking_proba(self, df: pd.DataFrame) -> np.ndarray:
        p_outcome     = self._safe_proba(self.outcome_model, df)
        p_bilstm      = self._safe_proba(self.bilstm_model, df)
        p_player_form = self._safe_proba(self.player_form_model, df)
        meta_X = np.hstack([p_outcome, p_bilstm, p_player_form])
        meta_X = np.nan_to_num(meta_X, nan=1.0 / 3.0)
        return self.meta_learner.predict_proba(meta_X)

    @staticmethod
    def _safe_proba(model, df: pd.DataFrame) -> np.ndarray:
        """Call predict_proba; on failure return uniform 1/3 distribution."""
        try:
            if model is None:
                raise ValueError("model is None")
            p = model.predict_proba(df)
            if p.shape[1] == 3:
                return p
            # shape guard
            raise ValueError(f"unexpected proba shape {p.shape}")
        except Exception:
            return np.full((len(df), 3), 1.0 / 3.0, dtype=np.float32)
