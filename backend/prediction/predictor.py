"""
Predictor — loads all trained models at startup and serves predictions.

Decouples model loading from the FastAPI route so models are loaded once
(at app startup via lifespan) rather than on every request.

Usage (in main.py lifespan):
    predictor = Predictor()
    predictor.load()

Usage (in route):
    result = predictor.predict(upcoming_df, finished_df)
"""

import os
import numpy as np
import pandas as pd

from data_processing.feature_engineering import build_features
from data_processing.player_feature_engineering import build_player_features
from models.match_outcome_model import MatchOutcomeModel
from models.goals_model import GoalsModel, MultiLineGoalsModel
from models.goals_distribution_model import GoalsDistributionModel
from models.bilstm_model import BiLSTMModel
from models.player_form_model import PlayerFormModel
from models.ensemble_model import EnsembleModel

MODEL_DIR = "saved_models"


class Predictor:
    def __init__(self):
        self.outcome_model:      MatchOutcomeModel | None     = None
        self.goals_model:        GoalsModel | None            = None
        self.multiline_model:    MultiLineGoalsModel | None   = None
        self.dist_model:         GoalsDistributionModel | None = None
        self.bilstm_model:       BiLSTMModel | None           = None
        self.player_form_model:  PlayerFormModel | None       = None
        self.ensemble:           EnsembleModel | None         = None
        self._loaded = False

    def load(self) -> None:
        """Load all available saved models. Missing models are silently skipped."""
        def _path(name: str) -> str:
            return os.path.join(MODEL_DIR, name)

        if os.path.exists(_path("match_outcome.pkl")):
            self.outcome_model = MatchOutcomeModel()
            self.outcome_model.load(_path("match_outcome.pkl"))

        # Load per-competition variants — keyed by competition ID string
        self._comp_models: dict[str, MatchOutcomeModel] = {}
        for fname in os.listdir(MODEL_DIR):
            if fname.startswith("match_outcome_") and fname.endswith(".pkl") \
                    and "_label_encoder" not in fname:
                comp = fname[len("match_outcome_"):-len(".pkl")]
                try:
                    m = MatchOutcomeModel()
                    m.load(_path(fname))
                    self._comp_models[comp] = m
                except Exception:
                    pass

        if os.path.exists(_path("goals.pkl")):
            self.goals_model = GoalsModel()
            self.goals_model.load(_path("goals.pkl"))

        ml_dir = _path("goals_multiline")
        if os.path.isdir(ml_dir):
            self.multiline_model = MultiLineGoalsModel()
            self.multiline_model.load(ml_dir)

        if os.path.exists(_path("goals_distribution.pkl")):
            self.dist_model = GoalsDistributionModel.load(_path("goals_distribution.pkl"))
        else:
            self.dist_model = None

        if os.path.exists(_path("bilstm_model.pt")):
            self.bilstm_model = BiLSTMModel()
            self.bilstm_model.load(_path("bilstm_model.pt"))

        if os.path.exists(_path("player_form_model.pkl")):
            self.player_form_model = PlayerFormModel()
            self.player_form_model.load(_path("player_form_model.pkl"))

        # Ensemble weights — load if saved, otherwise use defaults
        self.ensemble = EnsembleModel(
            outcome_model=self.outcome_model,
            bilstm_model=self.bilstm_model,
            player_form_model=self.player_form_model,
        )
        if os.path.exists(_path("ensemble_weights.pkl")):
            self.ensemble.load_weights(_path("ensemble_weights.pkl"))

        self._loaded = True

    def predict(
        self,
        upcoming_df: pd.DataFrame,
        finished_df: pd.DataFrame,
        db=None,
        competition: str | None = None,
    ) -> list[dict]:
        """
        Returns a list of prediction dicts — one per upcoming match.

        Args:
            upcoming_df:  DataFrame of upcoming matches (result=None)
            finished_df:  DataFrame of finished matches (for feature context)
            db:           Optional SQLAlchemy session — used for player features
        """
        if not self._loaded:
            self.load()

        # Inject current snapshot odds into upcoming rows so the mkt_open_* features
        # get real values at inference time (they'd otherwise be 0.0 for all upcoming).
        upcoming_df = self._inject_snapshot_odds(upcoming_df, competition, db)

        combined = pd.concat([finished_df, upcoming_df], ignore_index=True)
        feature_df = build_features(combined, dist_model=self.dist_model, db=db)

        if db is not None:
            feature_df = build_player_features(feature_df, db, competition=competition)

        upcoming_feat = feature_df[feature_df["status"].isin(["SCHEDULED", "TIMED"])]

        # Swap in competition-specific outcome model when available
        comp_outcome_model = (
            self._comp_models.get(competition)
            if competition and hasattr(self, "_comp_models") else None
        ) or self.outcome_model

        # Use per-competition ensemble weights when competition is supplied
        active_ensemble = (
            self.ensemble.for_competition(competition)
            if competition else self.ensemble
        )
        # Patch the active ensemble's outcome_model with the competition-specific one
        if comp_outcome_model is not self.outcome_model:
            active_ensemble = type(active_ensemble).__new__(type(active_ensemble))
            active_ensemble.__dict__.update(self.ensemble.__dict__)
            active_ensemble.outcome_model = comp_outcome_model

        # --- Outcome probabilities ---
        if active_ensemble.outcome_model is not None or active_ensemble.bilstm_model is not None:
            ens_details = active_ensemble.predict_with_confidence(upcoming_feat)
            ensemble_proba    = ens_details["ensemble"]     # (n, 3) [H, D, A]
            outcome_proba     = ens_details["outcome"]
            bilstm_proba      = ens_details["bilstm"]
            player_form_proba = ens_details["player_form"]
        else:
            uniform = np.full((len(upcoming_feat), 3), 1.0 / 3.0)
            ensemble_proba = outcome_proba = bilstm_proba = player_form_proba = uniform

        # Goals model unchanged — uses default ensemble

        # --- SHAP explanations ---
        shap_explanations: list[list[dict]] = []
        if self.outcome_model is not None:
            shap_explanations = self.outcome_model.explain(upcoming_feat)
        if not shap_explanations:
            shap_explanations = [[] for _ in range(len(upcoming_feat))]

        # --- Goals / markets ---
        if self.goals_model is not None:
            goals_proba = self.goals_model.predict_proba(upcoming_feat)  # (n, 2) [under, over]
        else:
            goals_proba = np.full((len(upcoming_feat), 2), 0.5)

        # Multi-line O/U probabilities
        ml_probas: dict[float, np.ndarray] = {}
        if self.multiline_model is not None:
            ml_probas = self.multiline_model.all_lines_proba(upcoming_feat)

        # Per-team historical match counts for data quality warnings
        team_counts: dict[str, int] = {}
        if len(finished_df) > 0:
            for _, r in finished_df.iterrows():
                team_counts[r["home_team"]] = team_counts.get(r["home_team"], 0) + 1
                team_counts[r["away_team"]] = team_counts.get(r["away_team"], 0) + 1

        results = []
        for i, (_, row) in enumerate(upcoming_feat.iterrows()):
            home = row["home_team"]
            away = row["away_team"]

            h_prob = round(float(ensemble_proba[i][0]), 4)
            d_prob = round(float(ensemble_proba[i][1]), 4)
            a_prob = round(float(ensemble_proba[i][2]), 4)
            predicted_outcome = ["H", "D", "A"][int(np.argmax([h_prob, d_prob, a_prob]))]

            over25_prob = round(float(goals_proba[i][1]), 4)

            markets: dict = {}
            xg_range: dict = {}
            if self.dist_model is not None:
                try:
                    neutral = bool(row.get("is_neutral_venue", False))
                    markets = self.dist_model.all_markets(home, away, neutral_venue=neutral)
                    # Expected goals range from DC lambda values (Poisson 10th–90th percentile)
                    lam_h, lam_a = self.dist_model.get_lambdas(home, away, neutral_venue=neutral)
                    from scipy.stats import poisson as _poisson
                    xg_range = {
                        "home_xg": round(lam_h, 2),
                        "away_xg": round(lam_a, 2),
                        "home_low":  round(float(_poisson.ppf(0.10, lam_h)), 1),
                        "home_high": round(float(_poisson.ppf(0.90, lam_h)), 1),
                        "away_low":  round(float(_poisson.ppf(0.10, lam_a)), 1),
                        "away_high": round(float(_poisson.ppf(0.90, lam_a)), 1),
                        "total_xg":  round(lam_h + lam_a, 2),
                    }
                except Exception:
                    markets = {}

            btts_prob = round(float(markets.get("btts", {}).get("yes", over25_prob * 0.7)), 4)

            # Confidence interval from component model disagreement
            component_probs = np.array([
                [outcome_proba[i][0], outcome_proba[i][1], outcome_proba[i][2]],
                [bilstm_proba[i][0],  bilstm_proba[i][1],  bilstm_proba[i][2]],
                [player_form_proba[i][0], player_form_proba[i][1], player_form_proba[i][2]],
            ])
            prob_std = component_probs.std(axis=0)  # (3,)
            confidence = {
                "home_win_ci": [
                    round(max(0.0, float(h_prob - 1.65 * prob_std[0])), 4),
                    round(min(1.0, float(h_prob + 1.65 * prob_std[0])), 4),
                ],
                "draw_ci": [
                    round(max(0.0, float(d_prob - 1.65 * prob_std[1])), 4),
                    round(min(1.0, float(d_prob + 1.65 * prob_std[1])), 4),
                ],
                "away_win_ci": [
                    round(max(0.0, float(a_prob - 1.65 * prob_std[2])), 4),
                    round(min(1.0, float(a_prob + 1.65 * prob_std[2])), 4),
                ],
                "model_agreement": round(float(1.0 - prob_std.mean() * 3), 3),  # 1=perfect agreement
            }

            # Corners O/U market from rolling feature averages
            exp_corners = float(row.get("expected_corners", 9.0))
            corners_market = {
                "expected_total": round(exp_corners, 1),
                "over_8_5":  round(float(1.0 - np.exp(-max(0, exp_corners - 8.5))), 3),
                "over_9_5":  round(float(1.0 - np.exp(-max(0, exp_corners - 9.5))), 3),
                "over_10_5": round(float(1.0 - np.exp(-max(0, exp_corners - 10.5))), 3),
                "home_avg":  round(float(row.get("home_corners_avg", 5.0)), 1),
                "away_avg":  round(float(row.get("away_corners_avg", 4.0)), 1),
            }

            # Data quality warning
            home_count = team_counts.get(home, 0)
            away_count = team_counts.get(away, 0)
            quality_warnings = []
            if home_count < 10:
                quality_warnings.append(f"{home}: only {home_count} historical matches")
            if away_count < 10:
                quality_warnings.append(f"{away}: only {away_count} historical matches")

            results.append({
                "home_team":       home,
                "away_team":       away,
                "match_date":      str(row["match_date"]),
                "shap_features":   shap_explanations[i],
                "signals": {
                    "home_steam":         bool(float(row.get("home_steam", 0)) > 0.5),
                    "away_steam":         bool(float(row.get("away_steam", 0)) > 0.5),
                    "draw_steam":         bool(float(row.get("draw_steam", 0)) > 0.5),
                    "max_steam_pct":      round(float(row.get("max_steam", 0)) * 100, 1),
                    "match_importance":   round(float(row.get("match_importance", 0)), 3),
                    "home_newly_promoted":bool(float(row.get("home_newly_promoted", 0)) > 0.5),
                    "away_newly_promoted":bool(float(row.get("away_newly_promoted", 0)) > 0.5),
                    "home_relegation_danger": round(float(row.get("home_relegation_danger", 0)), 3),
                    "away_relegation_danger": round(float(row.get("away_relegation_danger", 0)), 3),
                    "home_title_pressure":    round(float(row.get("home_title_pressure", 0)), 3),
                    "away_title_pressure":    round(float(row.get("away_title_pressure", 0)), 3),
                },
                "outcome": {
                    "predicted":     predicted_outcome,
                    "home_win_prob": h_prob,
                    "draw_prob":     d_prob,
                    "away_win_prob": a_prob,
                },
                "confidence":       confidence,
                "xg_range":         xg_range,
                "corners":          corners_market,
                "data_quality": {
                    "home_matches": home_count,
                    "away_matches": away_count,
                    "warnings":     quality_warnings,
                    "low_data":     len(quality_warnings) > 0,
                },
                "component_probas": {
                    "outcome_model":     {
                        "H": round(float(outcome_proba[i][0]), 4),
                        "D": round(float(outcome_proba[i][1]), 4),
                        "A": round(float(outcome_proba[i][2]), 4),
                    },
                    "bilstm": {
                        "H": round(float(bilstm_proba[i][0]), 4),
                        "D": round(float(bilstm_proba[i][1]), 4),
                        "A": round(float(bilstm_proba[i][2]), 4),
                    },
                    "player_form": {
                        "H": round(float(player_form_proba[i][0]), 4),
                        "D": round(float(player_form_proba[i][1]), 4),
                        "A": round(float(player_form_proba[i][2]), 4),
                    },
                },
                "markets": {
                    "over_under":    markets.get("over_under", {}),
                    "btts":          markets.get("btts", {"yes": btts_prob, "no": round(1 - btts_prob, 4)}),
                    "dnb":           markets.get("dnb", {}),
                    "wbtts":         markets.get("wbtts", {}),
                    "double_chance": markets.get("double_chance", {}),
                    "asian_handicap": markets.get("asian_handicap", {}),
                    "correct_score": markets.get("correct_score", []),
                    "corners":       corners_market,
                    "ml_over_under": {
                        str(line): {
                            "over":  round(float(ml_probas[line][i][1]), 4),
                            "under": round(float(ml_probas[line][i][0]), 4),
                        }
                        for line in ml_probas
                        if i < len(ml_probas[line])
                    },
                },
                "_meta": {
                    "predicted_over_2_5": bool(goals_proba[i][1] >= 0.5),
                    "over_2_5_prob":      over25_prob,
                    "predicted_btts":     bool(btts_prob >= 0.5),
                    "btts_prob":          btts_prob,
                    "predicted_outcome":  predicted_outcome,
                    "home_win_prob":      h_prob,
                    "draw_prob":          d_prob,
                    "away_win_prob":      a_prob,
                },
            })

        return results

    def _inject_snapshot_odds(
        self,
        upcoming_df: pd.DataFrame,
        competition: str | None,
        db,
    ) -> pd.DataFrame:
        """
        Fill opening_home/draw/away_odds columns from the OddsSnapshot table for
        upcoming matches that don't already have odds set. This ensures the
        mkt_open_* features get real market probabilities at inference time.
        """
        if db is None or competition is None:
            return upcoming_df
        try:
            from app.models import OddsSnapshot
            from data_collection.odds_client import _norm
            rows = db.query(OddsSnapshot).filter(
                OddsSnapshot.competition == competition
            ).all()
            snap_map = {(_norm(r.home_team), _norm(r.away_team)): r for r in rows}
            if not snap_map:
                return upcoming_df

            df = upcoming_df.copy()
            for idx, row in df.iterrows():
                if row.get("opening_home_odds"):
                    continue  # already has odds, don't overwrite
                key = (_norm(str(row.get("home_team", ""))),
                       _norm(str(row.get("away_team", ""))))
                snap = snap_map.get(key)
                if snap:
                    df.at[idx, "opening_home_odds"] = snap.home_odds
                    df.at[idx, "opening_draw_odds"] = snap.draw_odds
                    df.at[idx, "opening_away_odds"] = snap.away_odds
            return df
        except Exception:
            return upcoming_df


# Module-level singleton — loaded once by app lifespan
_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
        _predictor.load()
    return _predictor
