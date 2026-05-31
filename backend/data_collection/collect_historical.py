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
    
def _derive_match_statistics(home_score: Optional[int], away_score: Optional[int]) -> tuple[Optional[int], Optional[bool], Optional[bool], Optional[bool]]:
    """Derive additional match statistics from the raw match data."""
    if home_score is None or away_score is None:
       return (None, None, None, None)
    else:
        total = home_score + away_score
        return (total,
                total > 1.5,
                total > 2.5,
                home_score > 0 and away_score > 0)
        


def collect_historical_data(competition_id: str):
    matches = get_matches(competition_id)
    teams = get_teams(competition_id)
    
   
    with SessionLocal() as session:
        for team_data in teams:
            team = Team(id=team_data["id"], name=team_data["name"], competition=competition_id, country=team_data["area"]["name"])
            session.merge(team)

        for match_data in matches:
            total, over_1_5, over_2_5, btts = _derive_match_statistics(
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
                btts=btts,
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


STAGE_MAP = {
    "GROUP_STAGE":          "Group Stage",
    "LAST_32":              "R32",
    "LAST_16":              "R16",
    "QUARTER_FINALS":       "QF",
    "SEMI_FINALS":          "SF",
    "THIRD_PLACE":          "Third Place",
    "FINAL":                "Final",
}

INTERNATIONAL_COMPETITIONS = {"WC", "EC", "UNL", "CA"}


def collect_international_data(competition_id: str, season: int = None):
    """
    Collects matches for international competitions (WC, EC, UNL, CA).
    Sets is_neutral_venue=True for knockout rounds and tournament group stages.
    Stores tournament_stage and tournament_group from the API response.
    """
    matches = get_matches(competition_id, season)

    with SessionLocal() as session:
        for match_data in matches:
            total, over_1_5, over_2_5, btts = _derive_match_statistics(
                match_data["score"]["fullTime"]["home"],
                match_data["score"]["fullTime"]["away"],
            )
            stage_raw  = match_data.get("stage", "")
            stage      = STAGE_MAP.get(stage_raw, stage_raw)
            group      = match_data.get("group", "")
            # Normalise group names: "Group A" → "A", "GROUP_A" → "A"
            if group:
                if group.upper().startswith("GROUP_"):
                    group = group[6:]
                elif group.upper().startswith("GROUP "):
                    group = group.split()[-1]

            home_name = match_data["homeTeam"].get("name")
            away_name = match_data["awayTeam"].get("name")
            if not home_name or not away_name:
                continue  # skip undecided knockout bracket slots

            match = Match(
                id=match_data["id"],
                match_date=datetime.fromisoformat(match_data["utcDate"].replace("Z", "+00:00")),
                matchday=match_data.get("matchday"),
                home_team_id=match_data["homeTeam"].get("id"),
                away_team_id=match_data["awayTeam"].get("id"),
                home_team=home_name,
                away_team=away_name,
                home_team_score=match_data["score"]["fullTime"]["home"],
                away_team_score=match_data["score"]["fullTime"]["away"],
                competition=competition_id,
                season=int(match_data["season"]["startDate"][:4]),
                status=match_data["status"],
                result=RESULT_MAP.get(match_data["score"].get("winner")),
                total_goals=total,
                over_1_5_goals=over_1_5,
                over_2_5_goals=over_2_5,
                btts=btts,
                is_neutral_venue=True,
                tournament_stage=stage or None,
                tournament_group=group or None,
            )
            session.merge(match)
        session.commit()
    print(f"International data collected for {competition_id} (season={season})")


def run():
    init_db()

    collect_historical_data("PL")
    collect_historical_data("PD")

    print("Data collection complete.")


def run_international(seasons: list[int] | None = None):
    """Collect all international competition data."""
    init_db()
    for comp in ["WC", "EC", "UNL", "CA"]:
        if seasons:
            for s in seasons:
                try:
                    collect_international_data(comp, s)
                except Exception as e:
                    print(f"  {comp}/{s} skipped: {e}")
        else:
            try:
                collect_international_data(comp)
            except Exception as e:
                print(f"  {comp} skipped: {e}")
    print("International data collection complete.")


if __name__ == "__main__":
    import sys
    if "--international" in sys.argv:
        run_international()
    else:
        run()