"""
football-data.org API client
Docs: https://www.football-data.org/documentation/quickstart
"""
import time
import requests
from app.config import get_settings

settings = get_settings()

BASE_URL = "https://api.football-data.org/v4"

HEADERS = {
    "X-Auth-Token": settings.football_data_api_key
}


def _get(endpoint: str) -> dict:
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, headers=HEADERS)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:300]}")  # first 300 chars
    response.raise_for_status()
    time.sleep(settings.api_rate_limit_delay)
    return response.json()

def get_matches(competition_id: str, season: int = None) -> list[dict]:
    endpoint = f"/competitions/{competition_id}/matches"
    if season:
        endpoint += f"?season={season}"
    data = _get(endpoint)
    return data.get("matches", [])


def get_standings(competition_id: str, season: int = None) -> list[dict]:
    endpoint = f"/competitions/{competition_id}/standings"
    if season:
        endpoint += f"?season={season}"
    data = _get(endpoint)
    return data.get("standings", [])


def get_teams(competition_id: str, season: int = None) -> list[dict]:
    endpoint = f"/competitions/{competition_id}/teams"
    if season:
        endpoint += f"?season={season}"
    data = _get(endpoint)
    return data.get("teams", [])