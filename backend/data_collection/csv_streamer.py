import requests
import io
import pandas as pd

DIVISION_MAP = {
    # Top flight — Big 5
    "PL":  "E0",
    "PD":  "SP1",
    "BL1": "D1",
    "SA":  "I1",
    "FL1": "F1",
    # Second tier — Big 5
    "ELC": "E1",    # EFL Championship
    "BL2": "D2",    # 2. Bundesliga
    "PD2": "SP2",   # La Liga 2
    "SB":  "I2",    # Serie B
    "FL2": "F2",    # Ligue 2
    "SPL": "SC0",   # Scottish Premiership
    # Third tier UK
    "EL1": "E2",    # EFL League One
    "EL2": "E3",    # EFL League Two
    # Other European top-flights (soft books, real edge potential)
    "ERE": "N1",    # Eredivisie (Netherlands)
    "JPL": "B1",    # Jupiler Pro League (Belgium)
    "PPL": "P1",    # Primeira Liga (Portugal)
    "TSL": "T1",    # Süper Lig (Turkey)
    "GSL": "G1",    # Super League (Greece)
}

def _build_csv_url(competition_id: str, season: int) -> str:
    season_str = str(season)[2:] + str(season + 1)[2:]
    division = DIVISION_MAP[competition_id]
    return f"https://www.football-data.co.uk/mmz4281/{season_str}/{division}.csv"

def stream_csv(competition_id: str, year: int) -> pd.DataFrame:
    url = _build_csv_url(competition_id, year)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text))
