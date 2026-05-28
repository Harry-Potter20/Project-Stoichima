from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Match, Prediction
import pandas as pd
from datetime import datetime
from data_processing.feature_engineering import build_features
from models.match_outcome_model import MatchOutcomeModel
from models.goals_model import GoalsModel
from models.goals_distribution_model import GoalsDistributionModel

router = APIRouter()


def _load_models():
    outcome_model = MatchOutcomeModel()
    outcome_model.load("saved_models/match_outcome.pkl")

    goals_model = GoalsModel()
    goals_model.load("saved_models/goals.pkl")

    dist_model = GoalsDistributionModel.load("saved_models/goals_distribution.pkl")

    return outcome_model, goals_model, dist_model


@router.get("/predictions/{competition_id}")
def get_predictions(competition_id: str, db: Session = Depends(get_db)):
    upcoming = db.query(Match).filter(
        Match.competition == competition_id,
        Match.status.in_(["SCHEDULED", "TIMED"])
    ).all()

    finished = db.query(Match).filter(
        Match.competition == competition_id,
        Match.status == "FINISHED"
    ).all()

    if not upcoming:
        raise HTTPException(status_code=404, detail="No upcoming matches found")

    finished_df = pd.DataFrame([{
        "match_date": match.match_date,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_team_score": match.home_team_score,
        "away_team_score": match.away_team_score,
        "home_team_xG": match.home_team_xG,
        "away_team_xG": match.away_team_xG,
        "result": match.result,
        "season": match.season,
        "status": match.status,
    } for match in finished])

    upcoming_df = pd.DataFrame([{
        "match_date": match.match_date,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_team_score": None,
        "away_team_score": None,
        "home_team_xG": None,
        "away_team_xG": None,
        "result": None,
        "season": match.season,
        "status": match.status,
    } for match in upcoming])

    combined_df = pd.concat([finished_df, upcoming_df], ignore_index=True)
    df = build_features(combined_df)
    upcoming_features = df[df["status"].isin(["SCHEDULED", "TIMED"])]

    outcome_model, goals_model, dist_model = _load_models()

    outcome_preds       = outcome_model.predict(upcoming_features)
    outcome_labels      = outcome_model.decode(outcome_preds)
    outcome_proba       = outcome_model.predict_proba(upcoming_features)
    goals_proba         = goals_model.predict_proba(upcoming_features)  # [under, over]

    results = []
    for i, (_, row) in enumerate(upcoming_features.iterrows()):
        home = row["home_team"]
        away = row["away_team"]

        # Distribution model markets
        markets = dist_model.all_markets(home, away)

        h_prob = round(float(outcome_proba[i][0]), 4)
        d_prob = round(float(outcome_proba[i][1]), 4)
        a_prob = round(float(outcome_proba[i][2]), 4)
        over25_prob  = round(float(goals_proba[i][1]), 4)
        btts_prob    = round(float(markets["btts"]["yes"]), 4)

        prediction_record = Prediction(
            competition=competition_id,
            home_team=home,
            away_team=away,
            match_date=row["match_date"],
            predicted_outcome=str(outcome_labels[i]),
            home_win_prob=h_prob,
            draw_prob=d_prob,
            away_win_prob=a_prob,
            predicted_over_2_5=bool(goals_proba[i][1] >= 0.5),
            over_2_5_prob=over25_prob,
            predicted_btts=bool(markets["btts"]["yes"] >= 0.5),
            btts_prob=btts_prob,
        )
        db.merge(prediction_record) if prediction_record.match_date else db.add(prediction_record)

        results.append({
            "home_team": home,
            "away_team": away,
            "match_date": str(row["match_date"]),
            "outcome": {
                "predicted": str(outcome_labels[i]),
                "home_win_prob": h_prob,
                "draw_prob": d_prob,
                "away_win_prob": a_prob,
            },
            "markets": {
                "over_under": markets["over_under"],
                "btts": markets["btts"],
                "double_chance": markets["double_chance"],
                "asian_handicap": markets["asian_handicap"],
                "correct_score": markets["correct_score"],
            },
        })

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {"competition": competition_id, "predictions": results}
