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
from api.routes.live import router as live_router
from api.routes.player_props import router as player_props_router
from api.routes.bet_builder_v2 import router as parlay_v2_router
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

        def _index(name: str, table: str, cols: str, unique: bool = False):
            kind = "UNIQUE INDEX" if unique else "INDEX"
            conn.execute(text(
                f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({cols})"
            ))

        # ── matches: columns added after initial schema ──────────────────────
        _add("matches", "is_neutral_venue",   "INTEGER DEFAULT 0")
        _add("matches", "tournament_stage",   "TEXT")
        _add("matches", "tournament_group",   "TEXT")
        _add("matches", "b365_home",          "REAL")
        _add("matches", "b365_draw",          "REAL")
        _add("matches", "b365_away",          "REAL")
        _add("matches", "ps_home",            "REAL")
        _add("matches", "ps_draw",            "REAL")
        _add("matches", "ps_away",            "REAL")
        _add("matches", "max_home",           "REAL")
        _add("matches", "max_draw",           "REAL")
        _add("matches", "max_away",           "REAL")
        _add("matches", "avg_home",           "REAL")
        _add("matches", "avg_draw",           "REAL")
        _add("matches", "avg_away",           "REAL")
        _add("matches", "api_football_id",    "INTEGER")
        _add("matches", "sport",              "TEXT DEFAULT 'football'")
        _add("matches", "referee",            "TEXT")
        _add("matches", "opening_home_odds",  "REAL")
        _add("matches", "opening_draw_odds",  "REAL")
        _add("matches", "opening_away_odds",  "REAL")
        _add("matches", "btts",               "INTEGER")
        _add("matches", "over_1_5_goals",     "INTEGER")
        _add("matches", "home_team_corners",  "INTEGER")
        _add("matches", "away_team_corners",  "INTEGER")

        # ── BetLog columns ───────────────────────────────────────────────────
        _add("bet_log", "closing_home_odds", "REAL")
        _add("bet_log", "closing_draw_odds",  "REAL")
        _add("bet_log", "closing_away_odds",  "REAL")
        _add("bet_log", "clv_pct",        "REAL")
        _add("bet_log", "source",         "TEXT DEFAULT 'auto'")
        _add("bet_log", "notes",          "TEXT")
        _add("bet_log", "tags",           "TEXT")
        _add("bet_log", "market",         "TEXT DEFAULT '1x2'")
        _add("bet_log", "stake_amount",   "REAL")

        # ── OddsSnapshot columns ─────────────────────────────────────────────
        _add("odds_snapshots", "bk_home",    "TEXT")
        _add("odds_snapshots", "bk_draw",    "TEXT")
        _add("odds_snapshots", "bk_away",    "TEXT")
        _add("odds_snapshots", "arb_margin", "REAL")

        # ── Prediction columns ───────────────────────────────────────────────
        _add("predictions", "model_version", "TEXT")
        _add("predictions", "sport",         "TEXT DEFAULT 'football'")

        # ── Live tracking columns (idempotent — Base.metadata.create_all
        # already created the tables; these ALTERs cover cases where the
        # tables existed before columns were added in subsequent revisions).
        _add("live_match_state", "prematch_lambda_home", "REAL")
        _add("live_match_state", "prematch_lambda_away", "REAL")
        _add("live_match_state", "home_yellow_cards",    "INTEGER DEFAULT 0")
        _add("live_match_state", "away_yellow_cards",    "INTEGER DEFAULT 0")

        # ── Indexes for hot query paths ───────────────────────────────────────
        # Predictions route: filter matches by competition + status
        _index("ix_matches_comp_status", "matches", "competition, status")
        # Scheduler resolution: look up finished matches by competition + date
        _index("ix_matches_comp_date",   "matches", "competition, match_date")
        # BetLog daily Kelly query: filter by created_at date
        _index("ix_bet_log_created_at",  "bet_log", "created_at")
        # Prediction resolution: look up by competition + match date
        _index("ix_predictions_comp_date", "predictions", "competition, match_date")

        # ── Unique constraint: one prediction per fixture per run ─────────────
        # Prevents duplicate rows when the endpoint is polled repeatedly.
        # Dedup existing rows first so the unique index creation never fails on
        # data that predates the constraint (keeps the row with the lowest id).
        idx_exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_predictions_fixture'")
        ).fetchone()
        if not idx_exists:
            conn.execute(text(
                "DELETE FROM predictions WHERE id NOT IN ("
                "  SELECT MIN(id) FROM predictions"
                "  GROUP BY competition, home_team, away_team, match_date"
                ")"
            ))
        _index(
            "uq_predictions_fixture",
            "predictions",
            "competition, home_team, away_team, match_date",
            unique=True,
        )

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
app.include_router(live_router,        prefix="/api/v1", tags=["live"])
app.include_router(player_props_router, prefix="/api/v1", tags=["player_props"])
app.include_router(parlay_v2_router,    prefix="/api/v1", tags=["parlay_v2"])

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


@app.post("/admin/backup", tags=["admin"])
def manual_backup(tag: str = "manual"):
    """Create a timestamped DB backup. Returns path + total backup count."""
    from utils.db_backup import backup_db, list_backups
    path = backup_db(tag)
    return {"backup_path": path, "total_backups": len(list_backups())}


@app.get("/admin/backups", tags=["admin"])
def list_db_backups():
    """List existing DB backups, newest first."""
    from utils.db_backup import list_backups
    return {"backups": list_backups()}


@app.post("/admin/refresh-friendlies", tags=["admin"])
def manual_refresh_friendlies():
    """Manually trigger the FRIENDLY/MAR/BSA refresh + resolution + morning digest."""
    from scheduler import _refresh_daily_friendlies
    _refresh_daily_friendlies()
    return {"status": "friendly refresh triggered (check logs + Telegram)"}


@app.post("/admin/live-poll", tags=["admin"])
def manual_live_poll():
    """Trigger an immediate live-match poll (test the per-minute job)."""
    from data_collection.live_match_ingest import run_live_poll
    return run_live_poll()


@app.post("/admin/wc-digest", tags=["admin"])
def manual_wc_digest():
    """Manually trigger today's WC Telegram digest (test the daily 08:00 UTC job)."""
    from scheduler import _send_daily_wc_digest
    _send_daily_wc_digest()
    return {"status": "wc digest fired (check Telegram)"}


@app.post("/admin/wc-preview", tags=["admin"])
def manual_wc_preview(n: int = 8):
    """
    Send a Telegram preview of the next N upcoming WC/EC matches with
    probabilities — ignores the 24h window used by the daily digest.
    Useful before the tournament starts.
    """
    from scheduler import _send_tournament_preview
    _send_tournament_preview(n)
    return {"status": f"preview fired for next {n} matches"}


@app.post("/admin/prematch-alerts", tags=["admin"])
def manual_prematch_alerts():
    """Manually trigger pre-match alerts (matches starting in 30–45 min)."""
    from scheduler import _send_prematch_telegram_reminders
    _send_prematch_telegram_reminders()
    return {"status": "prematch alerts fired"}


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


@app.post("/admin/clear-pending-bets", tags=["admin"])
def clear_pending_bets(competition: str | None = None):
    """
    Delete pending (unsettled) BetLog rows. Backs up the DB first.
    Pass ?competition=WC to scope deletion to one competition.
    """
    from utils.db_backup import backup_db
    from app.database import SessionLocal
    from app.models import BetLog
    backup_path = backup_db(f"clear_pending_{competition or 'all'}")
    with SessionLocal() as db:
        q = db.query(BetLog).filter(BetLog.won.is_(None))
        if competition:
            q = q.filter(BetLog.competition == competition)
        n = q.delete()
        db.commit()
    return {"deleted": n, "competition": competition or "all", "backup": backup_path}


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
    # Prediction.match_date is a DateTime column → Python datetime; compare directly.
    recent   = [p for p in resolved if p.match_date and p.match_date >= cutoff]
    historic = [p for p in resolved if p.match_date and p.match_date < cutoff]

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
