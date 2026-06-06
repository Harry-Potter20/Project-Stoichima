"""
Daily fixture ingest — pulls upcoming matches for selected leagues and merges
into the Match table.

Used for the daily beta test loop:
  python -m data_collection.api_football_daily --days 7 --leagues 10 200 71 128

League IDs (API-Football):
  10  → International Friendlies
  200 → Morocco Botola Pro
  71  → Brazil Serie A
  128 → Argentina Primera
  253 → MLS
  98  → Japan J1
  292 → Korea K1
"""
from __future__ import annotations
import argparse
import logging
import time
from datetime import date, datetime, timedelta
import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import Match
from data_processing.team_normalizer import normalise_team_name

log = logging.getLogger(__name__)
BASE_URL = "https://v3.football.api-sports.io"

# API-Football league_id → (our competition code, is_international)
LEAGUE_MAP: dict[int, tuple[str, bool]] = {
    10:  ("FRIENDLY", True),    # International Friendlies (men)
    32:  ("WCQ_EU",   True),    # WC Qualification Europe
    33:  ("WCQ_AF",   True),    # WC Qualification Africa
    34:  ("WCQ_AS",   True),    # WC Qualification Asia
    35:  ("WCQ_SA",   True),    # WC Qualification S. America
    36:  ("WCQ_CC",   True),    # WC Qualification CONCACAF
    71:  ("BSA",      False),   # Brazil Serie A — Brasileirão
    72:  ("BSB",      False),   # Brazil Serie B
    128: ("APD",      False),   # Argentina Primera División
    253: ("MLS",      False),   # MLS
    98:  ("JPL",      False),   # Japan J1 (already in our DB)
    292: ("KRL",      False),   # Korea K League 1
    218: ("AUS",      False),   # A-League
    103: ("NOR",      False),   # Norway Eliteserien
    113: ("SWE",      False),   # Sweden Allsvenskan
    188: ("ISL",      False),   # Iceland Urvalsdeild
    119: ("DEN",      False),   # Denmark Superliga
    357: ("IRE",      False),   # Ireland Premier
    200: ("MAR",      False),   # Morocco Botola Pro
}

STATUS_MAP = {
    "Match Finished": "FINISHED",
    "Match Finished AET": "FINISHED",
    "Match Finished AP": "FINISHED",
    "Not Started": "SCHEDULED",
    "Time to be defined": "TIMED",
    "Postponed": "POSTPONED",
    "Match Cancelled": "CANCELLED",
    "Abandoned": "CANCELLED",
    "First Half, Kick Off": "IN_PLAY",
    "Halftime": "HT",
    "Second Half, 2nd Half Started": "IN_PLAY_2H",
    "Extra Time": "AET",
    "Penalty In Progress": "PEN",
    "Match Suspended": "SUSPENDED",
    "Match Interrupted": "SUSPENDED",
}


def _result_from_score(h, a):
    if h is None or a is None: return None
    if h > a: return "H"
    if h < a: return "A"
    return "D"


def _fetch_by_date(league: int, season: int, day: str, key: str) -> list[dict]:
    url = f"{BASE_URL}/fixtures?league={league}&season={season}&date={day}"
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=20)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        log.warning("AF %s/%s/%s errors: %s", league, season, day, d["errors"])
    return d.get("response", [])


def _fetch_by_date_all(day: str, key: str) -> list[dict]:
    """Fetch ALL fixtures for a date — free tier supports this even for current season."""
    url = f"{BASE_URL}/fixtures?date={day}"
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=20)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        log.warning("AF date=%s errors: %s", day, d["errors"])
    return d.get("response", [])


def _to_match(fix: dict, comp: str, is_intl: bool) -> Match:
    home   = normalise_team_name(fix["teams"]["home"]["name"])
    away   = normalise_team_name(fix["teams"]["away"]["name"])
    h_goal = fix["goals"]["home"]
    a_goal = fix["goals"]["away"]
    status_long = fix["fixture"]["status"]["long"]
    status = STATUS_MAP.get(status_long, status_long.upper())
    total = (h_goal + a_goal) if (h_goal is not None and a_goal is not None) else None
    over_1_5 = int(total >= 2) if total is not None else None
    over_2_5 = int(total >= 3) if total is not None else None
    btts = (
        int(h_goal > 0 and a_goal > 0)
        if (h_goal is not None and a_goal is not None) else None
    )

    return Match(
        id=900_000_000 + fix["fixture"]["id"],
        match_date=datetime.fromisoformat(fix["fixture"]["date"].replace("Z", "+00:00")).replace(tzinfo=None),
        home_team=home,
        away_team=away,
        home_team_score=h_goal,
        away_team_score=a_goal,
        competition=comp,
        season=int(fix["league"]["season"]),
        status=status,
        result=_result_from_score(h_goal, a_goal),
        total_goals=total,
        over_1_5_goals=over_1_5,
        over_2_5_goals=over_2_5,
        btts=btts,
        is_neutral_venue=1 if is_intl else 0,
        api_football_id=fix["fixture"]["id"],
    )


def run(leagues: list[int], days: int = 7, lookback_days: int = 2) -> dict:
    """
    Fetch fixtures by DATE (not by league+season — free tier blocks 2025+).
    Filter the returned set by league_id against our LEAGUE_MAP. This lets us
    pick up current-season fixtures the per-league query rejects.

    Range: from (today - lookback_days) through (today + days - 1). The lookback
    is important for overnight result capture — without it we miss matches that
    finished after yesterday's polling window.
    """
    s = get_settings()
    key = s.api_football_key
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY not set")

    league_set = set(leagues)
    today = date.today()
    pulled = {"total": 0, "by_league": {}}
    with SessionLocal() as db:
        for off in range(-lookback_days, days):
            day = (today + timedelta(days=off)).isoformat()
            try:
                fixtures = _fetch_by_date_all(day, key)
            except Exception as e:
                log.warning("AF date fetch failed %s: %s", day, e)
                fixtures = []
            kept = [fx for fx in fixtures if fx.get("league", {}).get("id") in league_set]
            if kept:
                for fix in kept:
                    league_id = fix["league"]["id"]
                    comp, is_intl = LEAGUE_MAP.get(league_id, (f"L{league_id}", False))
                    try:
                        m = _to_match(fix, comp, is_intl)
                        db.merge(m)
                        key = f"L{league_id} ({comp})"
                        pulled["by_league"][key] = pulled["by_league"].get(key, 0) + 1
                        pulled["total"] += 1
                    except Exception as e:
                        log.warning("merge fail: %s", e)
                db.commit()
                print(f"  {day}: kept {len(kept)} / {len(fixtures)} fixtures")
            else:
                print(f"  {day}: {len(fixtures)} fixtures total, 0 matched our leagues")
            time.sleep(0.4)

    for k, n in sorted(pulled["by_league"].items(), key=lambda x: -x[1]):
        print(f"    {k}: {n}")
    return pulled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--leagues", type=int, nargs="+",
                    default=[10, 32, 33, 34, 35, 36, 71, 128, 253, 98, 292, 218,
                             103, 113, 188, 119, 357, 200])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    result = run(args.leagues, args.days)
    print(f"\nTotal: {result['total']} fixtures merged")


if __name__ == "__main__":
    main()
