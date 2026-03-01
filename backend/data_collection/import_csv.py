from app.database import SessionLocal
from app.models import Match
from data_collection.collect_historical import _derive_match_statistics
from data_collection.csv_streamer import stream_csv
import pandas as pd
import numpy as np


CSV_COLUMN_NAMES = {
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "Date": "match_date",
    "FTHG": "home_team_score",
    "FTAG": "away_team_score",
    "FTR": "result",
    "HS": "home_team_shots",
    "AS": "away_team_shots",
    "HST": "home_team_shots_on_target",
    "AST": "away_team_shots_on_target",
    "HC": "home_team_corners",
    "AC": "away_team_corners",
    "HY": "home_team_yellow_cards",
    "AY": "away_team_yellow_cards",
    "HR": "home_team_red_cards",
    "AR": "away_team_red_cards",
    "HF": "home_team_fouls",
    "AF": "away_team_fouls"
}   


def _clean(value):
    """Convert NaN to None for database storage."""
    if value is None:
        return None
    try:
        if np.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    return value

def import_csv_to_dataframe(file_path: str) -> pd.DataFrame:
    return pd.read_csv(file_path)


def import_csv(file_path, competition_id, season):
    df = import_csv_to_dataframe(file_path)
    df = map_csv_columns(df)
    save_df_to_db(df, competition_id, season)

def save_df_to_db(df, competition_id, season):
    with SessionLocal() as session:
        for _, row in df.iterrows():
            total, over_1_5, over_2_5 = _derive_match_statistics(row["home_team_score"], row["away_team_score"])
            match = Match(
                id=_generate_match_id(row["home_team"], row["away_team"], row["match_date"]),
                home_team=row["home_team"],
                away_team=row["away_team"],
                match_date=pd.to_datetime(row["match_date"], dayfirst=True),
                season=season,
                status="FINISHED",
                competition=competition_id,
                total_goals=total,
                over_1_5_goals=over_1_5,
                over_2_5_goals=over_2_5,
                home_team_score=_clean(row["home_team_score"]),
                away_team_score=_clean(row["away_team_score"]),
                home_team_shots=_clean(row.get("home_team_shots")),
                away_team_shots=_clean(row.get("away_team_shots")),
                home_team_shots_on_target=_clean(row.get("home_team_shots_on_target")),
                away_team_shots_on_target=_clean(row.get("away_team_shots_on_target")),
                home_team_corners=_clean(row.get("home_team_corners")),
                away_team_corners=_clean(row.get("away_team_corners")),
                home_team_yellow_cards=_clean(row.get("home_team_yellow_cards")),
                away_team_yellow_cards=_clean(row.get("away_team_yellow_cards")),
                home_team_red_cards=_clean(row.get("home_team_red_cards")),
                away_team_red_cards=_clean(row.get("away_team_red_cards")),
                home_team_fouls=_clean(row.get("home_team_fouls")),
                away_team_fouls=_clean(row.get("away_team_fouls")),
                result=_clean(row["result"])
            )
            session.merge(match)
        session.commit()

def import_all_seasons(competitions:list, seasons:list):
    for competition in competitions:
        for season in seasons:
            print(f"Fetching {competition} {season}...")
            df = stream_csv(competition, season)
            df = map_csv_columns(df)
            save_df_to_db(df, competition, season)
            
    print("All seasons imported.")

def map_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=CSV_COLUMN_NAMES)
    return df

def _generate_match_id(home_team: str, away_team: str, match_date: str) -> int:
    return hash(f"{home_team}_{away_team}_{match_date}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4:
        import_csv(sys.argv[1], sys.argv[2], int(sys.argv[3]))
        print("Import complete.")
    else:
        import_all_seasons(
            competitions=["PL", "PD"],
            seasons=[2019, 2020, 2021, 2022, 2023, 2024]
        )