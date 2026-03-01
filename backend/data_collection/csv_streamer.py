import requests
import io
import pandas as pd

DIVISION_MAP = {
    "PL": "E0",
    "PD": "SP1"
}

def _build_csv_url(competition_id: str, season: int) -> str:
    season_str = str(season)[2:] + str(season + 1)[2:]
    division = DIVISION_MAP[competition_id]
    return f"https://www.football-data.co.uk/mmz4281/{season_str}/{division}.csv"

def stream_csv(competition_id: str, year: int) -> pd.DataFrame:
    url = _build_csv_url(competition_id, year)
    response = requests.get(url)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text))
