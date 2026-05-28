import mlflow
import mlflow.sklearn
import mlflow.xgboost
import pandas as pd

from app.database import SessionLocal
from app.models import Match
from data_processing.feature_engineering import build_features
from models.match_outcome_model import MatchOutcomeModel, FEATURES as OUTCOME_FEATURES, CUTOFF_SEASON
from models.goals_model import GoalsModel, FEATURES as GOALS_FEATURES
from models.goals_distribution_model import GoalsDistributionModel
from mlflow_utils import model_run, log_dataset_info, transition_model_to_production


def _load_matches() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (feature_df, raw_scores_df) for finished matches."""
    db = SessionLocal()
    matches = db.query(Match).filter(Match.status == "FINISHED").all()
    db.close()

    feature_df = pd.DataFrame([{
        "home_team":       m.home_team,
        "away_team":       m.away_team,
        "match_date":      m.match_date,
        "season":          m.season,
        "home_team_score": m.home_team_score,
        "away_team_score": m.away_team_score,
        "home_team_xG":    m.home_team_xG,
        "away_team_xG":    m.away_team_xG,
        "result":          m.result,
        "over_2_5_goals":  m.over_2_5_goals,
        "btts":            m.btts,
    } for m in matches])

    raw_df = pd.DataFrame([{
        "home_team":       m.home_team,
        "away_team":       m.away_team,
        "match_date":      m.match_date,
        "home_team_score": m.home_team_score,
        "away_team_score": m.away_team_score,
    } for m in matches])

    return feature_df, raw_df


def train_goals_distribution(raw_df: pd.DataFrame):
    print("\n[1/3] GoalsDistributionModel (Dixon-Coles)")
    with model_run("goals_distribution", tags={"model_type": "dixon_coles"}):
        log_dataset_info(raw_df)
        mlflow.log_params({
            "model":        "Dixon-Coles Poisson",
            "time_decay_xi": 0.0018,
            "max_goals":    8,
            "optimizer":    "L-BFGS-B",
        })

        model = GoalsDistributionModel()
        model.fit(raw_df)
        model.save("saved_models/goals_distribution.pkl")

        mlflow.log_artifact("saved_models/goals_distribution.pkl", artifact_path="models")
        mlflow.log_params({
            "n_teams":        len(model.teams),
            "home_advantage": round(model.home_advantage, 4),
            "rho":            round(model.rho, 4),
        })

        # Dixon-Coles is a custom serialized model — not sklearn/xgboost native.
        # The .pkl artifact is tracked per run (above). Log the run_id as a tag
        # so we can reload the exact version used in production if needed.
        run_id = mlflow.active_run().info.run_id
        mlflow.set_tag("artifact_pkl", f"runs:/{run_id}/models/goals_distribution.pkl")

    print("  ✓ saved_models/goals_distribution.pkl")


def train_match_outcome(df: pd.DataFrame):
    print("\n[2/3] MatchOutcomeModel (XGBoost + SMOTE)")
    with model_run("match_outcome", tags={"model_type": "xgboost_classifier"}) as run:
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")

        mlflow.log_params({
            "model":          "XGBClassifier",
            "n_estimators":   200,
            "learning_rate":  0.05,
            "eval_metric":    "mlogloss",
            "smote":          True,
            "cutoff_season":  CUTOFF_SEASON,
            "features":       OUTCOME_FEATURES,
            "n_features":     len(OUTCOME_FEATURES),
        })

        model = MatchOutcomeModel()
        model.train(df)
        metrics = model.evaluate(df)
        model.save("saved_models/match_outcome.pkl")

        mlflow.log_metrics(metrics)
        mlflow.xgboost.log_model(
            xgb_model=model.model,
            name="match_outcome",
            registered_model_name="MatchOutcomeModel",
        )
        mlflow.log_artifact("saved_models/match_outcome.pkl",          artifact_path="pkl")
        mlflow.log_artifact("saved_models/match_outcome_label_encoder.pkl", artifact_path="pkl")

        if _registry_available():
            transition_model_to_production("MatchOutcomeModel")

        print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")


def train_goals_model(df: pd.DataFrame):
    print("\n[3/3] GoalsModel (RandomForest Over/Under 2.5)")
    with model_run("goals_over_under", tags={"model_type": "random_forest_classifier"}):
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")

        mlflow.log_params({
            "model":         "RandomForestClassifier",
            "cutoff_season": CUTOFF_SEASON,
            "features":      GOALS_FEATURES,
            "n_features":    len(GOALS_FEATURES),
        })

        model = GoalsModel()
        model.train(df)
        metrics = model.evaluate(df)
        model.save("saved_models/goals.pkl")

        mlflow.log_metrics(metrics)
        mlflow.sklearn.log_model(
            sk_model=model.model,
            name="goals_over_under",
            registered_model_name="GoalsModel",
        )
        mlflow.log_artifact("saved_models/goals.pkl", artifact_path="pkl")

        if _registry_available():
            transition_model_to_production("GoalsModel")

        print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")


def _registry_available() -> bool:
    """Model registry works with SQLite and remote tracking stores."""
    uri = mlflow.get_tracking_uri()
    return not uri.startswith("file://")


def run():
    feature_df, raw_df = _load_matches()
    feature_df = build_features(feature_df)

    train_goals_distribution(raw_df)
    train_match_outcome(feature_df)
    train_goals_model(feature_df)

    print("\nAll models trained. Run `mlflow ui` to explore experiments.")


if __name__ == "__main__":
    run()
