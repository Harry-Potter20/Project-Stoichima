from app.database import Base, SessionLocal, engine
from app.models import Match, Team
from data_collection.football_data_api import get_matches, get_teams
from datetime import datetime
from typing import Optional

RESULT_MAP = {
                "HOME_TEAM": "H",
                "AWAY_TEAM": "A",
                "DRAW": "D"
            }

def init_db():
    """Initialize the database."""
    Base.metadata.create_all(bind=engine)
    
def _derive_match_statistics(home_score: Optional[int], away_score: Optional[int]) -> tuple[Optional[int], Optional[bool], Optional[bool]]:
    """Derive additional match statistics from the raw match data."""
    if home_score is None or away_score is None:
       return (None, None, None)
    else:
        total = home_score + away_score
        return (total,
                total > 1.5,
                total > 2.5)
        


def collect_historical_data(competition_id: str):
    matches = get_matches(competition_id)
    teams = get_teams(competition_id)
    
   
    with SessionLocal() as session:
        for team_data in teams:
            team = Team(id=team_data["id"], name=team_data["name"], competition=competition_id, country=team_data["area"]["name"])
            session.merge(team)

        for match_data in matches:
            total, over_1_5, over_2_5 = _derive_match_statistics(
                match_data["score"]["fullTime"]["home"],
                match_data["score"]["fullTime"]["away"]
            )
            
            match = Match(
                id=match_data["id"],
                match_date=datetime.fromisoformat(match_data["utcDate"].replace("Z", "+00:00")),
                matchday=match_data.get("matchday"),
                home_team_id=match_data["homeTeam"]["id"],
                away_team_id=match_data["awayTeam"]["id"],
                home_team=match_data["homeTeam"]["name"],
                away_team=match_data["awayTeam"]["name"],
                home_team_score=match_data["score"]["fullTime"]["home"],
                away_team_score=match_data["score"]["fullTime"]["away"],
                home_team_xG=match_data.get("homeTeamXG"),
                away_team_xG=match_data.get("awayTeamXG"),
                competition=competition_id,
                season=match_data["season"]["startDate"][:4],
                status=match_data["status"],
                result=RESULT_MAP.get(match_data["score"]["winner"]),
                total_goals=total,
                over_1_5_goals=over_1_5,
                over_2_5_goals=over_2_5,
                home_team_shots=match_data.get("homeTeamShots"),
                away_team_shots=match_data.get("awayTeamShots"),
                home_team_shots_on_target=match_data.get("homeTeamShotsOnTarget"),
                away_team_shots_on_target=match_data.get("awayTeamShotsOnTarget"),
                home_team_corners=match_data.get("homeTeamCorners"),
                away_team_corners=match_data.get("awayTeamCorners"),
                home_team_yellow_cards=match_data.get("homeTeamYellowCards"),
                away_team_yellow_cards=match_data.get("awayTeamYellowCards"),
                home_team_red_cards=match_data.get("homeTeamRedCards"),
                away_team_red_cards=match_data.get("awayTeamRedCards"),
                home_team_fouls=match_data.get("homeTeamFouls"),
                away_team_fouls=match_data.get("awayTeamFouls")
            )
            session.merge(match)

        session.commit()
    print(f"Historical data collected for {competition_id}")


def run():
    init_db()
   
    collect_historical_data("PL")  # Premier League
    collect_historical_data("PD")  # La Liga
   
    print("Data collection complete.")

if __name__ == "__main__":
    run()