from app.database import SessionLocal
from app.models import Match
from data_processing.feature_engineering import build_features
from models.match_outcome_model import MatchOutcomeModel
from models.goals_model import GoalsModel
import pandas as pd

def run():
    # 1. load data
    db = SessionLocal()
    matches = db.query(Match).filter(
        Match.status == "FINISHED"
    ).all()
    db.close()

    # 2. build dataframe
    df = pd.DataFrame([{
        "home_team": match.home_team,
        "away_team": match.away_team,
        "match_date": match.match_date,
        "season": match.season,
        "home_team_score": match.home_team_score,
        "away_team_score": match.away_team_score,
        "result": match.result,
        "over_2_5_goals": match.over_2_5_goals
    } for match in matches])

    # 3. build features
    df = build_features(df)

    # 4. train and save outcome model
    outcome_model = MatchOutcomeModel()
    outcome_model.train(df)
    outcome_model.save("saved_models/match_outcome.pkl")

    # 5. train and save goals model
    goals_model = GoalsModel()
    goals_model.train(df)
    goals_model.save("saved_models/goals.pkl")

if __name__ == "__main__":
    run()