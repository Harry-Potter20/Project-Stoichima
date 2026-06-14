# Stoichima

A full-stack football match prediction system. Combines XGBoost ensemble models, a Dixon-Coles goals distribution model, a Bi-LSTM sequence model, and a player-form layer to predict match outcomes, goals markets, and correct scores across six leagues plus international tournaments.

---

## Features

### Predictions
| Market | Detail |
|---|---|
| Match outcome | H / D / A probabilities with confidence rating |
| Over/Under | Four lines: 1.5 / 2.5 / 3.5 / 4.5 |
| BTTS | Both-teams-to-score probability |
| Correct score | Top-10 scorelines from Dixon-Coles matrix |
| Asian handicap | Lines −2 to +2 in 0.5 steps |
| Double chance | 1X / X2 / 12 |

### Signals
- **Steam moves** — sharp-money line movement (≥8% odds compression) flagged per side
- **Match importance** — continuous title-race / relegation-battle pressure score; "6-pointer" badge when > 0.5
- **Newly promoted** — rolling stats regressed 40% toward league average for first-season clubs
- **Managerial change** — recency of manager change as a feature (new-manager bounce effect)

### Leagues
Premier League · La Liga · Bundesliga · Serie A · Ligue 1 · WC 2026 / Euros

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI · SQLAlchemy · SQLite |
| ML | XGBoost · scikit-learn · imbalanced-learn · PyTorch (BiLSTM) · SciPy (Dixon-Coles) |
| Calibration | `CalibratedClassifierCV` (isotonic) on all classifiers |
| Scheduling | APScheduler — fixture refresh, resolution, ELO recalibration, retrain |
| Data | football-data.co.uk CSVs · Understat (xG / shot data) · API-Football (injuries) |
| Frontend | React · Recharts · Tailwind CSS |

---

## Models

```
MatchOutcomeModel     XGBoost (300 est, calibrated) — H/D/A, 72+ features
GoalsModel            RandomForest — binary Over/Under 2.5 fallback
MultiLineGoalsModel   Calibrated RF per line: 1.5 / 2.5 / 3.5 / 4.5
GoalsDistributionModel Dixon-Coles Poisson — full scoreline matrix → all markets
XGModel               XGBoost — shot-level xG (trained on Understat data)
PlayerFormModel       XGBoost — includes player squad xG/xA availability features
BiLSTMModel           PyTorch — last-10-match sequence (bidirectional, hidden=64×2)
EnsembleModel         Soft-voting average over outcome + player form + BiLSTM
```

Per-competition models are trained and saved separately when ≥ 200 labelled matches exist; the global model is used as fallback.

### Feature groups (72+)
Rolling form · goals scored/conceded · H2H · ELO · home advantage coefficient ·
xG + shot quality (own model) · corners · days rest / congestion · table position ·
title/relegation pressure · season progress · opening + closing market lines ·
odds movement ratios · Dixon-Coles H/D/A signal · referee tendencies ·
managerial change recency · newly promoted flag · steam move flags · SHAP explanations

---

## API Endpoints

```
GET  /api/v1/predictions/{competition}      Upcoming match predictions
GET  /api/v1/accuracy/{competition}         Accuracy metrics (resolved predictions)
GET  /api/v1/calibration/{competition}      Reliability curves + Brier scores
GET  /api/v1/clv/{competition}              Closing line value tracker
GET  /api/v1/bankroll/                      Kelly criterion bankroll management
GET  /api/v1/bets/value-scan               Cross-competition value bet finder
GET  /api/v1/odds/steam                     Steam move scanner
GET  /api/v1/predictions/export.csv         Predictions CSV download
GET  /api/v1/tournament/{id}/predictions    Tournament round predictions
GET  /api/v1/tournament/{id}/simulate       Monte Carlo stage probabilities
GET  /admin/feature-importance              XGBoost gain-based feature rankings
GET  /admin/model-drift                     30-day rolling accuracy vs historical baseline
GET  /admin/model-info                      Model version, training metadata, live Brier
POST /admin/retrain                         Trigger background model retrain
POST /admin/refresh                         Immediate fixture refresh + prediction resolution
POST /admin/recalibrate-elo                 Re-derive ELO ratings from finished results
```

Interactive docs: `http://localhost:8000/docs`

---

## Setup

### Backend

```bash
python3 -m venv stoichima
source stoichima/bin/activate
pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and fill in:

```
API_FOOTBALL_KEY=your_key   # optional — injury/suspension data
TELEGRAM_BOT_TOKEN=         # optional — digest alerts
TELEGRAM_CHAT_ID=
ADMIN_API_KEY=              # optional — when set, /admin/* requires X-Admin-Key header
```

Start the server:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm start          # http://localhost:3000
```

---

## Data Pipeline

Run once (or nightly) to populate the database before training:

```bash
cd backend

# Import historical CSVs from football-data.co.uk (PL, PD, BL1, SA, FL1)
python data_collection/import_csv.py

# Scrape Understat shot data for xG model training
python data_collection/understat_scraper.py

# Scrape player match stats (for player form model)
python data_collection/understat_player_scraper.py

# Fetch injury/suspension data (requires API_FOOTBALL_KEY)
python data_collection/api_football_client.py
```

---

## Training

```bash
cd backend
python train_models.py
```

Training steps (in order):
1. XGModel — shot-level xG from Understat shots table
2. GoalsDistributionModel — Dixon-Coles Poisson fit
3. MatchOutcomeModel — global XGBoost (Optuna tuning + isotonic calibration)
4. Per-competition models — PL / PD / BL1 / SA / FL1 (≥200 matches each)
5. MultiLineGoalsModel — O/U 1.5 / 2.5 / 3.5 / 4.5
6. GoalsModel — binary O/U 2.5 fallback
7. PlayerFormModel — squad xG / availability features
8. BiLSTMModel — 10-match sequence model (PyTorch)
9. EnsembleModel — soft-vote calibration

Saved to `backend/saved_models/`.

---

## Tests

```bash
cd backend
pytest tests/ -q
```

20 tests covering the full request/response pipeline, model loading, scheduler hooks, and DB migrations.

---

## Project Structure

```
backend/
  app/              FastAPI app, config, models (SQLAlchemy), DB
  api/routes/       Prediction, accuracy, calibration, bankroll, tournament, …
  data_collection/  CSV importer, Understat scraper, API-Football client
  data_processing/  Feature engineering, player features, sequence builder
  models/           ML model classes
  prediction/       Predictor (model orchestration), tournament simulator
  saved_models/     Trained model artefacts (git-ignored)
  tests/            pytest suite

frontend/
  src/
    components/     PredictionsTable, CalibrationChart, TournamentBracket,
                    TeamFormChart, AccuracyTracker, BetSlip, …
    App.js          Tab navigation, league selector
```
