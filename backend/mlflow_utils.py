"""
MLflow tracking utilities for Stoichima.

Local usage:
    python3 train_models.py          # logs to ./mlruns by default
    mlflow ui                        # view at http://localhost:5000

Switching to DagHub (remote, collaborative):
    Set in backend/.env:
        MLFLOW_TRACKING_URI=https://dagshub.com/<username>/<repo>.mlflow
        MLFLOW_TRACKING_USERNAME=<dagshub_username>
        MLFLOW_TRACKING_PASSWORD=<dagshub_token>
    Nothing else changes — all tracking calls below work identically.
"""
import os
import mlflow
from contextlib import contextmanager

EXPERIMENT_NAME = "stoichima-football-predictions"


def setup_tracking():
    """
    Configure the MLflow tracking URI from the environment, falling back to
    a local SQLite database so training works with zero config.
    """
    uri = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(EXPERIMENT_NAME)


@contextmanager
def model_run(run_name: str, tags: dict | None = None):
    """
    Context manager that wraps a training block in an MLflow run.
    Yields the active run so callers can log extra artefacts if needed.

    Usage:
        with model_run("match_outcome") as run:
            mlflow.log_params({...})
            model.train(df)
            mlflow.log_metrics({...})
            mlflow.log_artifact("saved_models/match_outcome.pkl")
    """
    setup_tracking()
    # MLflow 3.x awaits stale RUNNING runs with the same name before opening a
    # new one — this deadlocks after a killed training process. Terminate them.
    _terminate_stale_runs(run_name)
    if hasattr(mlflow, "flush_async_logging"):
        mlflow.flush_async_logging()
    with mlflow.start_run(run_name=run_name, tags=tags or {}) as run:
        yield run


def _terminate_stale_runs(run_name: str) -> None:
    """Mark any RUNNING runs with the given name as KILLED so they don't block."""
    try:
        exp = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
        if exp is None:
            return
        client = mlflow.tracking.MlflowClient()
        stale = client.search_runs(
            exp.experiment_id,
            filter_string=f"attributes.status = 'RUNNING' and tags.mlflow.runName = '{run_name}'",
        )
        for r in stale:
            client.set_terminated(r.info.run_id, status="KILLED")
    except Exception:
        pass


def log_dataset_info(df, label: str = "training"):
    """Log basic dataset statistics as MLflow params."""
    mlflow.log_params({
        f"{label}_rows":    len(df),
        f"{label}_seasons": sorted(df["season"].unique().tolist()) if "season" in df else "n/a",
    })


def transition_model_to_production(model_name: str):
    """
    Assign the 'production' alias to the latest registered model version.
    Uses the MLflow aliases API (stages were deprecated in MLflow 2.9).
    """
    client = mlflow.tracking.MlflowClient()
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        return
    latest = max(versions, key=lambda v: int(v.version))
    client.set_registered_model_alias(model_name, "production", latest.version)
    print(f"  → {model_name} v{latest.version} aliased as 'production'")
