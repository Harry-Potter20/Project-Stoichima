from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Match
import pandas as pd
from data_processing.feature_engineering import build_features
from models.match_outcome_model import MatchOutcomeModel
from models.goals_model import GoalsModel

router = APIRouter()

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
        "result": match.result,
        "season": match.season,
        "status": match.status
    } for match in finished])
    
    upcoming_df = pd.DataFrame([{
        "match_date": match.match_date,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "season": match.season,
        "result": None,
        "status": match.status,
        "home_team_score": None,
        "away_team_score": None
    } for match in upcoming])
    
    combined_df = pd.concat([finished_df, upcoming_df], ignore_index=True)
    df = build_features(combined_df)
    print("Status values in df:", df["status"].unique())
    upcoming_features = df[df["status"].isin(["SCHEDULED", "TIMED"])]
    
    outcome_model = MatchOutcomeModel()
    outcome_model.load("saved_models/match_outcome.pkl")

    goals_model = GoalsModel()
    goals_model.load("saved_models/goals.pkl")

    outcome_preds = outcome_model.predict(upcoming_features)
    outcome_preds = outcome_model.decode(outcome_preds)
    print("Upcoming features shape:", upcoming_features.shape)
    print("Columns:", upcoming_features.columns.tolist())
    print("Features needed:", ["home_goals_scored_avg", "away_goals_scored_avg", "home_attack_vs_away_defense", "away_attack_vs_home_defense"])
    goals_preds = goals_model.predict(upcoming_features)

    # Logic to get predictions for the given competition_id
    results = []
    for i, (_, row) in enumerate(upcoming_features.iterrows()):
        results.append({
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "match_date": str(row["match_date"]),
            "predicted_outcome": str(outcome_preds[i]),
            "predicted_over_2_5": bool(goals_preds[i])
        })
    return {"competition": competition_id, "predictions": results}
