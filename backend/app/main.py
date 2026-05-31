from contextlib import asynccontextmanager
import logging
import json
import time
from fastapi import FastAPI, Request
from app.config import get_settings


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts":      self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
            **({"exc": self.formatException(record.exc_info)} if record.exc_info else {}),
        })


def _configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Silence noisy libs
    for lib in ("uvicorn.access", "apscheduler.executors.default"):
        logging.getLogger(lib).setLevel(logging.WARNING)
from api.routes.predictions import router as predictions_router
from api.routes.accuracy import router as accuracy_router
from api.routes.tournaments import router as tournaments_router
from api.routes.calibration import router as calibration_router
from api.routes.clv import router as clv_router
from api.routes.bankroll import router as bankroll_router
from api.routes.tennis import router as tennis_router
from api.routes.basketball import router as basketball_router
from api.routes.telegram import router as telegram_router
from fastapi.middleware.cors import CORSMiddleware
from scheduler import create_scheduler

settings = get_settings()
_scheduler = create_scheduler()


def _run_db_migrations():
    """Add new columns to existing tables that predate them (SQLite-safe ALTER TABLE)."""
    from sqlalchemy import text
    from app.database import engine, Base
    # Create any brand-new tables (ManagerialChange, BasketballMatch, etc.)
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        def _add(table: str, col: str, typedef: str):
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))

        # BetLog new columns
        _add("bet_log", "closing_home_odds", "REAL")
        _add("bet_log", "closing_draw_odds",  "REAL")
        _add("bet_log", "closing_away_odds",  "REAL")
        _add("bet_log", "source",         "TEXT DEFAULT 'auto'")
        _add("bet_log", "notes",          "TEXT")
        _add("bet_log", "tags",           "TEXT")
        _add("bet_log", "market",         "TEXT DEFAULT '1x2'")
        _add("bet_log", "stake_amount",   "REAL")

        # OddsSnapshot new columns
        _add("odds_snapshots", "bk_home",    "TEXT")
        _add("odds_snapshots", "bk_draw",    "TEXT")
        _add("odds_snapshots", "bk_away",    "TEXT")
        _add("odds_snapshots", "arb_margin", "REAL")

        # Prediction new columns
        _add("predictions", "model_version", "TEXT")

        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    log = logging.getLogger("stoichima")
    _run_db_migrations()
    log.info("Loading ML models…")
    from prediction.predictor import get_predictor
    get_predictor()          # warm up — loads all models from disk once at startup
    log.info("Models ready.")
    _scheduler.start()
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(
    title="Football Predictor API",
    description="ML-powered match predictions for Premier League and La Liga",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000)
    logging.getLogger("stoichima.http").info(
        json.dumps({"method": request.method, "path": request.url.path,
                    "status": response.status_code, "ms": ms})
    )
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions_router, prefix="/api/v1", tags=["predictions"])
app.include_router(accuracy_router,    prefix="/api/v1", tags=["accuracy"])
app.include_router(tournaments_router, prefix="/api/v1", tags=["tournaments"])
app.include_router(calibration_router, prefix="/api/v1", tags=["calibration"])
app.include_router(clv_router,         prefix="/api/v1", tags=["clv"])
app.include_router(bankroll_router,    prefix="/api/v1", tags=["bankroll"])
app.include_router(tennis_router,      prefix="/api/v1", tags=["tennis"])
app.include_router(basketball_router,  prefix="/api/v1", tags=["basketball"])
app.include_router(telegram_router,    tags=["telegram"])

@app.get("/")
def root():
    return {
        "status": "ok",
        "environment": settings.environment,
        "leagues": [settings.premier_league_id, settings.la_liga_id]
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/admin/refresh", tags=["admin"])
def manual_refresh():
    """Trigger an immediate fixture refresh + prediction resolution outside the schedule."""
    from scheduler import _refresh_fixtures, _resolve_predictions
    _refresh_fixtures()
    _resolve_predictions()
    return {"status": "refresh complete"}


@app.post("/admin/settle", tags=["admin"])
def manual_settle():
    """Immediately settle all open BetLog rows against finished match results."""
    from scheduler import _resolve_predictions
    _resolve_predictions()
    return {"status": "settlement complete"}


@app.post("/admin/recalibrate-elo", tags=["admin"])
def recalibrate_elo(competition: str = "WC"):
    """
    Re-derive team ELO ratings from actual tournament results (K=40).
    Call after each round to keep R16+ predictions sharp.
    Busts prediction cache so the next request uses fresh ELO.
    """
    from app.database import SessionLocal
    from app.models import Match, EloRating
    from datetime import datetime
    import prediction.cache as pred_cache

    with SessionLocal() as db:
        finished = (
            db.query(Match)
            .filter(
                Match.competition == competition,
                Match.status == "FINISHED",
                Match.result.isnot(None),
            )
            .order_by(Match.match_date)
            .all()
        )
        if not finished:
            return {"status": "no finished matches", "competition": competition}

        K = 40  # higher K for tournaments (fast adaptation, limited sample)
        elo: dict[str, float] = {}
        for m in finished:
            h_elo = elo.get(m.home_team, 1500.0)
            a_elo = elo.get(m.away_team, 1500.0)
            exp_h = 1 / (1 + 10 ** ((a_elo - h_elo) / 400))
            act_h = 1.0 if m.result == "H" else (0.5 if m.result == "D" else 0.0)
            elo[m.home_team] = h_elo + K * (act_h - exp_h)
            elo[m.away_team] = a_elo + K * ((1 - act_h) - (1 - exp_h))

        now = datetime.utcnow()
        updated = 0
        for team, rating in elo.items():
            row = db.query(EloRating).filter(
                EloRating.team == team,
                EloRating.competition == competition,
            ).first()
            if row:
                row.elo = round(rating, 2)
                row.as_of_date = now
            else:
                db.add(EloRating(
                    team=team, competition=competition,
                    elo=round(rating, 2), as_of_date=now,
                ))
            updated += 1
        db.commit()

    pred_cache.invalidate()
    return {"status": "ok", "competition": competition, "teams_updated": updated}


@app.post("/admin/retrain", tags=["admin"])
def manual_retrain():
    """Kick off a background model retrain (runs train_models.py in a subprocess)."""
    from scheduler import _retrain_models
    _retrain_models()
    return {"status": "retrain started — check logs for progress"}


@app.get("/admin/retrain/status", tags=["admin"])
def retrain_status():
    """Quick health-check: is a retrain currently running?"""
    import subprocess
    procs = subprocess.run(
        ["pgrep", "-f", "train_models.py"], capture_output=True, text=True
    )
    running = bool(procs.stdout.strip())
    return {"running": running}


@app.get("/admin/model-drift", tags=["admin"])
def model_drift():
    """
    Compare rolling 30-day accuracy to the historical baseline.
    Returns drift_detected=True when recent accuracy drops >5pp below historical.
    """
    from app.database import SessionLocal
    from app.models import Prediction
    from datetime import datetime, timedelta
    import numpy as np

    db = SessionLocal()
    try:
        resolved = db.query(Prediction).filter(
            Prediction.actual_outcome.isnot(None)
        ).all()
    finally:
        db.close()

    if not resolved:
        return {"status": "no_data", "drift_detected": False}

    def _acc(preds):
        if not preds:
            return None
        return round(sum(1 for p in preds if p.predicted_outcome == p.actual_outcome) / len(preds), 4)

    cutoff = datetime.utcnow() - timedelta(days=30)
    recent   = [p for p in resolved if p.match_date and p.match_date >= cutoff.date()]
    historic = [p for p in resolved if p.match_date and p.match_date < cutoff.date()]

    hist_acc   = _acc(historic)
    recent_acc = _acc(recent)
    drift = (
        hist_acc is not None
        and recent_acc is not None
        and (hist_acc - recent_acc) > 0.05
    )
    return {
        "historic_accuracy":   hist_acc,
        "recent_30d_accuracy": recent_acc,
        "recent_n":            len(recent),
        "historic_n":          len(historic),
        "drift_detected":      drift,
        "drift_pp":            round((hist_acc - recent_acc) * 100, 1) if hist_acc and recent_acc else None,
        "status":              "drift_alert" if drift else "ok",
    }


@app.get("/admin/feature-importance", tags=["admin"])
def feature_importance():
    """Returns XGBoost gain-based feature importances from the loaded outcome model."""
    from prediction.predictor import get_predictor
    predictor = get_predictor()
    if predictor.outcome_model is None:
        return {"error": "outcome model not loaded"}
    imps = predictor.outcome_model.get_feature_importances()
    return {"features": imps, "n_features": len(imps)}


@app.get("/admin/model-info", tags=["admin"])
def model_info():
    """
    Returns model version, training metadata, Brier score, and feature list.
    Reads from saved_models/model_meta.json if it exists.
    """
    import os, json as _json
    from app.config import get_settings
    s = get_settings()

    meta_path = os.path.join(os.path.dirname(__file__), "..", "saved_models", "model_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = _json.load(f)

    # Live Brier score from DB
    brier = None
    try:
        from app.database import SessionLocal
        from app.models import Prediction
        from sqlalchemy import func as _f
        db = SessionLocal()
        resolved = db.query(Prediction).filter(
            Prediction.actual_outcome.isnot(None)
        ).all()
        db.close()
        if resolved:
            import numpy as np
            outcome_map = {"H": 0, "D": 1, "A": 2}
            scores = []
            for p in resolved:
                y = outcome_map.get(p.actual_outcome, -1)
                if y == -1:
                    continue
                probs = [p.home_win_prob, p.draw_prob, p.away_win_prob]
                one_hot = [1.0 if i == y else 0.0 for i in range(3)]
                scores.append(sum((pi - oi) ** 2 for pi, oi in zip(probs, one_hot)))
            if scores:
                brier = round(float(np.mean(scores)), 4)
    except Exception:
        pass

    return {
        "model_version":   s.model_version,
        "training_date":   meta.get("training_date"),
        "matches_trained": meta.get("matches_trained"),
        "features":        meta.get("features", []),
        "models":          meta.get("models", []),
        "brier_score":     brier,
        "brier_baseline":  0.6667,
        "brier_skill":     round(1 - brier / 0.6667, 4) if brier else None,
    }
