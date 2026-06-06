import os
# macOS: XGBoost and PyTorch each ship their own libomp. Without this, whichever
# loads second hangs or segfaults. Setting it here (before any import) is the
# standard workaround; it is safe for local training.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import mlflow
import mlflow.sklearn
import mlflow.xgboost
import numpy as np
import pandas as pd

from app.database import SessionLocal
from app.models import Match, Shot
from data_processing.feature_engineering import build_features
from data_processing.player_feature_engineering import build_player_features
from models.match_outcome_model import MatchOutcomeModel, FEATURES as OUTCOME_FEATURES, CUTOFF_SEASON
from models.goals_model import GoalsModel, MultiLineGoalsModel, FEATURES as GOALS_FEATURES
from models.goals_distribution_model import GoalsDistributionModel
from models.xg_model import XGModel
from models.player_form_model import PlayerFormModel, FEATURES as PLAYER_FEATURES
from models.bilstm_model import BiLSTMModel
from models.ensemble_model import EnsembleModel
from mlflow_utils import model_run, log_dataset_info, transition_model_to_production


def _load_matches() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (feature_df, raw_scores_df) for finished matches."""
    db = SessionLocal()
    matches = db.query(Match).filter(Match.status == "FINISHED").all()
    db.close()

    feature_df = pd.DataFrame([{
        "home_team":               m.home_team,
        "away_team":               m.away_team,
        "match_date":              m.match_date,
        "season":                  m.season,
        "competition":             m.competition,
        "status":                  m.status,
        "home_team_score":         m.home_team_score,
        "away_team_score":         m.away_team_score,
        "home_team_xG":            m.home_team_xG,
        "away_team_xG":            m.away_team_xG,
        "result":                  m.result,
        "over_2_5_goals":          m.over_2_5_goals,
        "btts":                    m.btts,
        "referee":                 m.referee,
        "opening_home_odds":       m.opening_home_odds,
        "opening_draw_odds":       m.opening_draw_odds,
        "opening_away_odds":       m.opening_away_odds,
        "b365_home":               m.b365_home,
        "b365_draw":               m.b365_draw,
        "b365_away":               m.b365_away,
        "home_team_yellow_cards":  m.home_team_yellow_cards,
        "away_team_yellow_cards":  m.away_team_yellow_cards,
        "home_team_red_cards":     m.home_team_red_cards,
        "away_team_red_cards":     m.away_team_red_cards,
    } for m in matches])

    raw_df = pd.DataFrame([{
        "home_team":        m.home_team,
        "away_team":        m.away_team,
        "match_date":       m.match_date,
        "home_team_score":  m.home_team_score,
        "away_team_score":  m.away_team_score,
        "competition":      m.competition,
        "is_neutral_venue": m.is_neutral_venue or 0,
    } for m in matches])

    return feature_df, raw_df


def _load_shots() -> pd.DataFrame:
    """Loads all shots from DB. Returns empty DataFrame if table is empty."""
    db = SessionLocal()
    shots = db.query(Shot).all()
    db.close()
    if not shots:
        return pd.DataFrame()
    return pd.DataFrame([{
        "understat_id":  s.understat_id,
        "home_team":     s.home_team,
        "away_team":     s.away_team,
        "player_team":   s.player_team,
        "x":             s.x,
        "y":             s.y,
        "result":        s.result,
        "shot_type":     s.shot_type,
        "situation":     s.situation,
        "last_action":   s.last_action,
        "understat_xg":  s.understat_xg,
    } for s in shots])


def train_xg_model(shots_df: pd.DataFrame) -> XGModel | None:
    """[0/5] XGModel — own xG from shot-level data."""
    if shots_df.empty:
        print("\n[0/5] XGModel — skipped (no shots in DB; run understat_scraper.py first)")
        return None

    print(f"\n[0/5] XGModel (XGBoost binary classifier — {len(shots_df):,} shots)")
    xg_model = XGModel()

    with model_run("xg_model", tags={"model_type": "xgboost_binary"}):
        mlflow.log_params({
            "model":         "XGBClassifier",
            "n_estimators":  400,
            "learning_rate": 0.05,
            "max_depth":     5,
            "n_shots":       len(shots_df),
            "goal_rate":     round((shots_df["result"].str.lower() == "goal").mean(), 4),
        })

        xg_model.train(shots_df)
        xg_model.save("saved_models/xg_model.pkl")

        val = xg_model.validate_vs_understat(shots_df)
        mlflow.log_metrics({
            "brier_own":       val["brier_own"],
            "brier_understat": val["brier_understat"],
        })
        mlflow.log_artifact("saved_models/xg_model.pkl", artifact_path="models")

        run_id = mlflow.active_run().info.run_id
        mlflow.set_tag("artifact_pkl", f"runs:/{run_id}/models/xg_model.pkl")

    print(f"  ✓ brier_own={val['brier_own']:.4f}  brier_understat={val['brier_understat']:.4f}")
    return xg_model


def train_goals_distribution(raw_df: pd.DataFrame):
    print("\n[1/6] GoalsDistributionModel (Dixon-Coles)")
    with model_run("goals_distribution", tags={"model_type": "dixon_coles"}):
        log_dataset_info(raw_df)
        mlflow.log_params({
            "model":         "Dixon-Coles Poisson",
            "time_decay_xi": 0.0018,
            "max_goals":     8,
            "optimizer":     "L-BFGS-B",
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

        run_id = mlflow.active_run().info.run_id
        mlflow.set_tag("artifact_pkl", f"runs:/{run_id}/models/goals_distribution.pkl")

    print("  ✓ saved_models/goals_distribution.pkl")
    return model


def train_goals_distribution_intl(raw_df: pd.DataFrame):
    """
    Dedicated DC model trained ONLY on international matches.
    Club-trained DC has ~50k matches that drown out the ~700 international
    matches in MLE fitting; team-strength parameters collapse toward zero,
    causing all international xG predictions to cluster around 1.0 goals.
    A separate intl-only fit gives realistic parameter spread.
    """
    print("\n[1b/6] GoalsDistributionModel-intl (Dixon-Coles, international only)")
    intl_codes = {"WC", "EC", "UNL", "AFC", "ASIA", "CA", "GOLD"}
    if "competition" in raw_df.columns:
        intl_df = raw_df[raw_df["competition"].isin(intl_codes)].copy()
    else:
        # raw_df may not include competition; rebuild from DB directly
        from app.models import Match
        db = SessionLocal()
        rows = db.query(Match).filter(
            Match.status == "FINISHED",
            Match.competition.in_(list(intl_codes)),
        ).all()
        db.close()
        intl_df = pd.DataFrame([{
            "home_team":       m.home_team,
            "away_team":       m.away_team,
            "match_date":      m.match_date,
            "home_team_score": m.home_team_score,
            "away_team_score": m.away_team_score,
            "is_neutral_venue": 1,
        } for m in rows])

    if len(intl_df) < 100:
        print(f"  skipped: only {len(intl_df)} international matches (need ≥100)")
        return None

    intl_df = intl_df.copy()
    intl_df["is_neutral_venue"] = 1   # international fixtures are neutral

    with model_run("goals_distribution_intl", tags={"model_type": "dixon_coles_intl"}):
        log_dataset_info(intl_df)
        model = GoalsDistributionModel()
        model.fit(intl_df)
        model.save("saved_models/goals_distribution_intl.pkl")
        mlflow.log_params({
            "n_matches":      len(intl_df),
            "n_teams":        len(model.teams),
            "home_advantage": round(model.home_advantage, 4),
            "rho":            round(model.rho, 4),
        })
    print(f"  ✓ saved_models/goals_distribution_intl.pkl ({len(model.teams)} teams)")
    return model


def train_match_outcome(df: pd.DataFrame):
    print("\n[2/6] MatchOutcomeModel (XGBoost + SMOTE + calibration)")
    with model_run("match_outcome", tags={"model_type": "xgboost_classifier"}):
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
        mlflow.log_artifact("saved_models/match_outcome.pkl",               artifact_path="pkl")
        mlflow.log_artifact("saved_models/match_outcome_label_encoder.pkl", artifact_path="pkl")

        if _registry_available():
            transition_model_to_production("MatchOutcomeModel")

    print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")


def train_player_form_model(df: pd.DataFrame):
    print("\n[3/6] PlayerFormModel (XGBoost + SMOTE + squad features + calibration)")
    has_player_data = any(
        col in df.columns and df[col].any()
        for col in ["home_squad_xg_avg", "home_availability_score"]
    )
    if not has_player_data:
        print("  skipped — player tables empty (run understat_player_scraper.py first)")
        return

    with model_run("player_form", tags={"model_type": "xgboost_player_form"}):
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")

        mlflow.log_params({
            "model":         "XGBClassifier",
            "n_estimators":  300,
            "learning_rate": 0.05,
            "smote":         True,
            "cutoff_season": CUTOFF_SEASON,
            "features":      PLAYER_FEATURES,
            "n_features":    len(PLAYER_FEATURES),
        })

        model = PlayerFormModel()
        model.train(df)
        metrics = model.evaluate(df)
        model.save("saved_models/player_form_model.pkl")

        mlflow.log_metrics(metrics)
        mlflow.xgboost.log_model(
            xgb_model=model.model,
            name="player_form",
            registered_model_name="PlayerFormModel",
        )
        mlflow.log_artifact("saved_models/player_form_model.pkl", artifact_path="pkl")

        if _registry_available():
            transition_model_to_production("PlayerFormModel")

    print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")


def train_bilstm(df: pd.DataFrame) -> BiLSTMModel:
    print(f"\n[4/6] BiLSTMModel (PyTorch — {len(df):,} matches)", flush=True)
    with model_run("bilstm", tags={"model_type": "bilstm_pytorch"}):
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")

        mlflow.log_params({
            "model":       "BiLSTM",
            "hidden_size": 64,
            "n_layers":    2,
            "window":      10,
            "dropout":     0.3,
            "epochs":      50,
            "patience":    7,
        })

        model = BiLSTMModel()
        model.train(df)
        model.save("saved_models/bilstm_model.pt")

        metrics = model.evaluate(df)
        if metrics:
            mlflow.log_metrics(metrics)
            print(f"  ✓ accuracy={metrics['bilstm_accuracy']:.3f}  macro_f1={metrics['bilstm_macro_f1']:.3f}")
        else:
            print("  ✓ saved_models/bilstm_model.pt (no eval — insufficient test data)")

        mlflow.log_artifact("saved_models/bilstm_model.pt", artifact_path="models")
        run_id = mlflow.active_run().info.run_id
        mlflow.set_tag("artifact_pt", f"runs:/{run_id}/models/bilstm_model.pt")

    return model


def train_ensemble(
    outcome_model: MatchOutcomeModel,
    bilstm_model: BiLSTMModel,
    player_form_model: PlayerFormModel,
    df: pd.DataFrame,
):
    print("\n[5/6] EnsembleModel (stacking meta-learner + soft-voting fallback)")
    with model_run("ensemble", tags={"model_type": "stacking_ensemble"}):
        mlflow.log_params({
            "strategy":           "stacking",
            "meta_learner":       "LogisticRegression",
            "weight_outcome":     0.35,
            "weight_bilstm":      0.40,
            "weight_player_form": 0.25,
        })

        ensemble = EnsembleModel(
            outcome_model, bilstm_model, player_form_model, use_stacking=True
        )
        train_df = df[df["season"] < CUTOFF_SEASON].dropna(subset=["result"])
        ensemble.fit_stacking(train_df)
        ensemble.save_weights("saved_models/ensemble_weights.pkl")

        # Quick accuracy check on test set
        test_df = df[df["season"] >= CUTOFF_SEASON].dropna(subset=["result"])
        if len(test_df) > 0:
            from sklearn.metrics import accuracy_score, f1_score
            label_map = {"H": 0, "D": 1, "A": 2}
            y_true = test_df["result"].map(label_map).values
            proba = ensemble.predict_proba(test_df)
            y_pred = proba.argmax(axis=1)
            acc = accuracy_score(y_true, y_pred)
            f1  = f1_score(y_true, y_pred, average="macro")
            mlflow.log_metrics({"ensemble_accuracy": round(acc, 4), "ensemble_macro_f1": round(f1, 4)})
            print(f"  ✓ accuracy={acc:.3f}  macro_f1={f1:.3f}")
        else:
            print("  ✓ saved_models/ensemble_weights.pkl")

        mlflow.log_artifact("saved_models/ensemble_weights.pkl", artifact_path="models")


def train_goals_model(df: pd.DataFrame):
    print("\n[6/6] GoalsModel (RandomForest Over/Under 2.5 + multi-line variants)")
    with model_run("goals_over_under", tags={"model_type": "random_forest_classifier"}):
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")

        mlflow.log_params({
            "model":         "RandomForestClassifier",
            "cutoff_season": CUTOFF_SEASON,
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

    # Multi-line model (1.5 / 2.5 / 3.5 / 4.5) — trained separately
    print("    Training multi-line O/U models…")
    ml_model = MultiLineGoalsModel()
    ml_model.train(df)
    ml_model.save("saved_models/goals_multiline/")
    print("  ✓ saved_models/goals_multiline/ (1.5, 2.5, 3.5, 4.5)")


def _compute_sample_weights(df: pd.DataFrame) -> np.ndarray:
    """
    Combined time-decay + CLV sample weights for training.

    Time decay: exponential with half-life of ~1.5 seasons (≈550 days).
    CLV boost:  bets where the model beat the closing line (CLV > 0) get
                upweighted by up to 2× — those matches carry stronger signal.
    """
    import numpy as np
    from datetime import datetime

    dates = pd.to_datetime(df["match_date"])
    now   = pd.Timestamp(datetime.utcnow())
    days  = (now - dates).dt.days.clip(lower=0).values
    half_life_days = 550.0
    time_weights = np.exp(-np.log(2) * days / half_life_days)

    # CLV boost — query BetLog for matches with positive CLV
    clv_weights = np.ones(len(df))
    try:
        from app.database import SessionLocal
        from app.models import BetLog
        db = SessionLocal()
        bets = db.query(BetLog).filter(BetLog.clv_pct.isnot(None)).all()
        db.close()

        if bets:
            clv_map: dict[tuple, float] = {}
            for b in bets:
                key = (
                    str(b.home_team).lower().strip(),
                    str(b.away_team).lower().strip(),
                    str(b.match_date)[:10],
                )
                clv_map[key] = max(clv_map.get(key, 0.0), float(b.clv_pct or 0.0))

            for i, row in df.iterrows():
                key = (
                    str(row.get("home_team", "")).lower().strip(),
                    str(row.get("away_team", "")).lower().strip(),
                    str(row.get("match_date", ""))[:10],
                )
                clv = clv_map.get(key, 0.0)
                if clv > 0:
                    clv_weights[df.index.get_loc(i)] = min(2.0, 1.0 + clv / 100.0)
    except Exception:
        pass  # CLV data unavailable — fall back to time weights only

    combined = time_weights * clv_weights
    # Normalise so the mean weight is 1.0 (preserves effective sample size)
    combined /= combined.mean()
    return combined


def train_per_competition_models(feature_df: pd.DataFrame):
    """
    Train competition-specific MatchOutcomeModel variants.
    Saved as saved_models/match_outcome_{COMP}.pkl alongside the global model.
    Skipped for competitions with fewer than 200 finished matches.
    """
    MIN_MATCHES = 200
    competitions = feature_df["competition"].dropna().unique()

    for comp in sorted(competitions):
        comp_df = feature_df[feature_df["competition"] == comp].copy()
        n = len(comp_df.dropna(subset=["result"]))
        if n < MIN_MATCHES:
            print(f"  skip {comp}: only {n} matches (need ≥{MIN_MATCHES})")
            continue

        print(f"\n  [{comp}] Training per-competition model ({n} matches)…")
        with model_run(f"match_outcome_{comp}", tags={"model_type": "xgboost_per_competition", "competition": comp}):
            mlflow.log_params({"competition": comp, "n_matches": n})
            m = MatchOutcomeModel()
            sw = _compute_sample_weights(comp_df)
            m.train(comp_df, sample_weight=sw)
            metrics = m.evaluate(comp_df)
            mlflow.log_metrics(metrics)
            path = f"saved_models/match_outcome_{comp}.pkl"
            m.save(path)
            print(f"    ✓ {path}  acc={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")


def _registry_available() -> bool:
    """Model registry works with SQLite and remote tracking stores."""
    uri = mlflow.get_tracking_uri()
    return not uri.startswith("file://")


def run():
    from data_processing.team_normalizer import normalise_team_name

    feature_df, raw_df = _load_matches()
    shots_df = _load_shots()

    # [0/5] Own xG model — trained before feature engineering so xG features work
    xg_model = train_xg_model(shots_df)

    # Normalise team names in raw_df so DC model uses same naming as feature_engineering
    raw_df["home_team"] = raw_df["home_team"].apply(normalise_team_name)
    raw_df["away_team"] = raw_df["away_team"].apply(normalise_team_name)

    # [1/6] Train DC model first — feature_engineering needs it for dc_* columns
    dist_model_inst = train_goals_distribution(raw_df)
    # [1b/6] Also fit a separate international-only DC so WC/EC predictions
    # aren't drowned out by club matches in the global MLE.
    train_goals_distribution_intl(raw_df)

    # Build all features in one pass, including DC probabilities
    db = SessionLocal()
    try:
        feature_df = build_features(
            feature_df,
            shots_df=shots_df if xg_model else None,
            xg_model=xg_model,
            dist_model=dist_model_inst,
            db=db,
        )
        feature_df = build_player_features(feature_df, db)
    finally:
        db.close()

    # Compute sample weights once — used by global + per-competition outcome models
    sample_weights = _compute_sample_weights(feature_df)

    outcome_model_inst = _train_and_return_outcome(feature_df, sample_weights)
    player_form_inst   = _train_and_return_player_form(feature_df)
    bilstm_inst        = train_bilstm(feature_df)

    train_ensemble(outcome_model_inst, bilstm_inst, player_form_inst, feature_df)
    train_goals_model(feature_df)

    print("\n[Per-competition models]")
    train_per_competition_models(feature_df)

    # Write model metadata for /admin/model-info
    import json as _json
    from datetime import datetime as _dt
    meta = {
        "training_date":   _dt.utcnow().isoformat() + "Z",
        "matches_trained": int(len(feature_df)),
        "models":          ["GoalsDistributionModel", "MatchOutcomeModel", "PlayerFormModel", "BiLSTMModel", "EnsembleModel", "GoalsModel"],
        "features":        OUTCOME_FEATURES,
    }
    os.makedirs("saved_models", exist_ok=True)
    with open("saved_models/model_meta.json", "w") as _f:
        _json.dump(meta, _f, indent=2)

    print("\nAll models trained. Run `mlflow ui` to explore experiments.")


def _train_and_return_outcome(
    df: pd.DataFrame,
    sample_weights=None,
    tune: bool = False,
) -> MatchOutcomeModel:
    """Thin wrapper so run() can get back the trained model instance."""
    print("\n[2/6] MatchOutcomeModel (XGBoost + SMOTE + calibration + time-decay weights)")

    model = MatchOutcomeModel()

    # Optional Optuna tuning — prune low-gain features afterward
    best_params = {}
    if tune:
        print("  Running Optuna hyperparameter search (50 trials, 5-min cap)…")
        best_params = MatchOutcomeModel.tune_hyperparams(df, n_trials=50, timeout=300)

    with model_run("match_outcome", tags={"model_type": "xgboost_classifier"}) as run:
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")
        mlflow.log_params({
            "model":          "XGBClassifier",
            "smote":          True,
            "calibration":    "isotonic",
            "sample_weights": sample_weights is not None,
            "optuna_tuned":   tune and bool(best_params),
            "n_features":     len(OUTCOME_FEATURES),
            **{f"xgb_{k}": v for k, v in best_params.items()},
        })

        model.train(df, sample_weight=sample_weights, xgb_params=best_params or None)

        # Feature importance pruning
        pruned = model.prune_low_importance_features(df, min_gain=0.001)
        if len(pruned) < len(OUTCOME_FEATURES):
            print(f"  Retraining on {len(pruned)} pruned features…")
            model.train(df, sample_weight=sample_weights,
                        xgb_params=best_params or None, feature_override=pruned)

        metrics = model.evaluate(df)

        # Conformal calibration: use the chronologically last 20% as calibration set
        df_sorted = df.sort_values("match_date") if "match_date" in df.columns else df
        calib_start = int(len(df_sorted) * 0.8)
        calib_df = df_sorted.iloc[calib_start:]
        if len(calib_df) >= 30:
            try:
                model.compute_conformal_q90(calib_df)
                mlflow.log_metric("conformal_q90", round(float(model._conformal_q90), 4))
                print(f"  ✓ conformal q90={model._conformal_q90:.4f}")
            except Exception as _ce:
                print(f"  ⚠ conformal calibration skipped: {_ce}")

        model.save("saved_models/match_outcome.pkl")
        mlflow.log_metrics({**metrics, "n_features_after_pruning": len(pruned)})
        mlflow.sklearn.log_model(
            sk_model=model.model,
            name="match_outcome",
            registered_model_name="MatchOutcomeModel",
        )
        mlflow.log_artifact("saved_models/match_outcome.pkl",               artifact_path="pkl")
        mlflow.log_artifact("saved_models/match_outcome_label_encoder.pkl", artifact_path="pkl")
        if _registry_available():
            transition_model_to_production("MatchOutcomeModel")
    print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")
    return model


def _train_and_return_player_form(df: pd.DataFrame) -> PlayerFormModel:
    has_player_data = any(
        col in df.columns and df[col].any()
        for col in ["home_squad_xg_avg", "home_availability_score"]
    )
    if not has_player_data:
        print("\n[3/6] PlayerFormModel — skipped (run understat_player_scraper.py first)")
        m = PlayerFormModel()
        if os.path.exists("saved_models/player_form_model.pkl"):
            m.load("saved_models/player_form_model.pkl")
        return m

    print("\n[3/6] PlayerFormModel (XGBoost + SMOTE + squad features + calibration)")
    with model_run("player_form", tags={"model_type": "xgboost_player_form"}):
        log_dataset_info(df[df["season"] < CUTOFF_SEASON], label="train")
        log_dataset_info(df[df["season"] >= CUTOFF_SEASON], label="test")
        mlflow.log_params({
            "model":         "XGBClassifier",
            "n_estimators":  300,
            "smote":         True,
            "calibration":   "isotonic",
            "cutoff_season": CUTOFF_SEASON,
            "features":      PLAYER_FEATURES,
            "n_features":    len(PLAYER_FEATURES),
        })
        model = PlayerFormModel()
        model.train(df)
        metrics = model.evaluate(df)
        model.save("saved_models/player_form_model.pkl")
        mlflow.log_metrics(metrics)
        mlflow.sklearn.log_model(
            sk_model=model.model,
            name="player_form",
            registered_model_name="PlayerFormModel",
        )
        mlflow.log_artifact("saved_models/player_form_model.pkl", artifact_path="pkl")
        if _registry_available():
            transition_model_to_production("PlayerFormModel")
    print(f"  ✓ accuracy={metrics['accuracy']:.3f}  macro_f1={metrics['macro_f1']:.3f}")
    return model


if __name__ == "__main__":
    run()
