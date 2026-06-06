"""
One-shot ETL: pull historical international tournament matches from API-Football
into our local Match table, mapped to our competition codes (WC/EC/UNL/CA).

API-Football league IDs:
  1  → World Cup
  4  → UEFA Euro
  5  → UEFA Nations League
  9  → Copa America

Run:
    python -m data_collection.api_football_international

Idempotent — uses Match.id (from API-Football fixture id, prefixed by source tag
to avoid colliding with football-data.org ids) and SQLAlchemy merge.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime
import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import Match
from data_processing.team_normalizer import normalise_team_name

log = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"

# (api_football_league_id, our_competition_code, season_year)
# Free-tier API-Football allows seasons 2022–2024 only for these competitions.
COMP_PLAN = [
    (1,  "WC",   2022),
    (4,  "EC",   2024),
    (5,  "UNL",  2022),
    (5,  "UNL",  2024),
    (6,  "AFC",  2023),   # Africa Cup of Nations 2023 (played early 2024)
    (7,  "ASIA", 2023),   # AFC Asian Cup 2023 (played early 2024)
    (9,  "CA",   2024),   # Copa America 2024
    (22, "GOLD", 2023),   # CONCACAF Gold Cup 2023
]

# Map API-Football status long → our internal status
STATUS_MAP = {
    "Match Finished":      "FINISHED",
    "Match Finished AET":  "FINISHED",
    "Match Finished AP":   "FINISHED",  # after penalties
    "Not Started":         "SCHEDULED",
    "Time to be defined":  "TIMED",
    "Postponed":           "POSTPONED",
    "Match Cancelled":     "CANCELLED",
    "Abandoned":           "CANCELLED",
}


def _result_from_score(h: int | None, a: int | None) -> str | None:
    if h is None or a is None:
        return None
    if h > a:
        return "H"
    if h < a:
        return "A"
    return "D"


def _fetch(league: int, season: int, key: str) -> list[dict]:
    url = f"{BASE_URL}/fixtures?league={league}&season={season}"
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        log.warning("API-Football errors for league=%d season=%d: %s",
                    league, season, data["errors"])
        return []
    return data.get("response", [])


def _to_match(fix: dict, comp: str, season: int) -> Match:
    home   = normalise_team_name(fix["teams"]["home"]["name"])
    away   = normalise_team_name(fix["teams"]["away"]["name"])
    h_goal = fix["goals"]["home"]
    a_goal = fix["goals"]["away"]
    status_long = fix["fixture"]["status"]["long"]
    status = STATUS_MAP.get(status_long, status_long.upper())

    total = (h_goal + a_goal) if (h_goal is not None and a_goal is not None) else None
    over_1_5 = int(total >= 2) if total is not None else None
    over_2_5 = int(total >= 3) if total is not None else None
    btts = int(h_goal > 0 and a_goal > 0) if (h_goal is not None and a_goal is not None) else None

    # Prefix with 9_ to namespace against football-data.org ids (which are <1M);
    # API-Football fixture ids are also large but we add 9e8 offset to be safe.
    match_id = 900_000_000 + fix["fixture"]["id"]

    return Match(
        id=match_id,
        match_date=datetime.fromisoformat(fix["fixture"]["date"].replace("Z", "+00:00")),
        home_team=home,
        away_team=away,
        home_team_score=h_goal,
        away_team_score=a_goal,
        competition=comp,
        season=season,
        status=status,
        result=_result_from_score(h_goal, a_goal),
        total_goals=total,
        over_1_5_goals=over_1_5,
        over_2_5_goals=over_2_5,
        btts=btts,
        is_neutral_venue=1,
        tournament_stage=fix.get("league", {}).get("round"),
        api_football_id=fix["fixture"]["id"],
    )


def run() -> dict:
    s = get_settings()
    key = s.api_football_key
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY not set in .env")

    pulled = {"total": 0, "by_comp": {}}
    with SessionLocal() as db:
        for league_id, comp, season in COMP_PLAN:
            try:
                fixtures = _fetch(league_id, season, key)
            except Exception as e:
                log.warning("Fetch failed for %s/%d: %s", comp, season, e)
                continue

            if not fixtures:
                continue

            n = 0
            for fix in fixtures:
                try:
                    m = _to_match(fix, comp, season)
                    db.merge(m)
                    n += 1
                except Exception as e:
                    log.warning("Skip fixture %s: %s", fix.get("fixture", {}).get("id"), e)
            db.commit()
            pulled["total"] += n
            pulled["by_comp"][f"{comp}/{season}"] = n
            print(f"  {comp}/{season}: {n} fixtures merged")
            time.sleep(0.5)  # gentle pacing under daily quota

    return pulled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(f"\nTotal: {result['total']} international matches pulled")
    for k, v in result["by_comp"].items():
        print(f"  {k}: {v}")
