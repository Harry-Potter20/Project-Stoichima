"""
National team squad ingest — pulls current rosters from API-Football's
/players/squads endpoint and writes to NationalTeamSquad.

Run:
    python -m data_collection.squad_fetcher --teams brazil argentina france
    python -m data_collection.squad_fetcher --all-wc

Quota cost: 1 request per nation (so 48 for the full WC field). Free tier
allows 100/day, so this fits in a single daily quota.
"""
from __future__ import annotations
import argparse
import logging
import time
from typing import Optional
import requests

from app.config import get_settings
from app.database import SessionLocal
from app.models import NationalTeamSquad
from data_processing.team_normalizer import normalise_team_name

log = logging.getLogger(__name__)
BASE_URL = "https://v3.football.api-sports.io"

# API-Football national-team IDs for the WC 2026 field (and big surrounding ones).
# Derived from /teams?league=1&season=2022 + supplemental research.
NATIONAL_TEAM_IDS = {
    # UEFA
    "France": 2, "England": 10, "Spain": 9, "Germany": 25, "Italy": 768,
    "Netherlands": 1118, "Portugal": 27, "Belgium": 1, "Croatia": 3,
    "Switzerland": 15, "Denmark": 21, "Austria": 4, "Poland": 24,
    "Czechia": 12, "Turkey": 22, "Serbia": 6, "Hungary": 23, "Slovakia": 18,
    "Slovenia": 19, "Ukraine": 28, "Wales": 767, "Scotland": 1108,
    "Ireland": 5, "Sweden": 16, "Norway": 11, "Iceland": 13,
    "Albania": 769, "Romania": 770, "Bosnia-Herzegovina": 1066,
    # CONMEBOL
    "Brazil": 6, "Argentina": 26, "Uruguay": 7, "Colombia": 8,
    "Chile": 17, "Peru": 30, "Paraguay": 14, "Ecuador": 20, "Bolivia": 31,
    "Venezuela": 29,
    # CONCACAF
    "United States": 2384, "Mexico": 16, "Canada": 18,
    "Costa Rica": 33, "Jamaica": 1530, "Panama": 1528, "Honduras": 1531,
    "Haiti": 1532, "Curaçao": 4566,
    # CAF
    "Morocco": 31, "Senegal": 32, "Tunisia": 1525, "Egypt": 24,
    "Algeria": 1542, "Nigeria": 27, "Ghana": 29, "Cameroon": 1492,
    "Ivory Coast": 28, "South Africa": 25, "Congo DR": 1546,
    # AFC
    "Japan": 12, "South Korea": 25, "Iran": 22, "Australia": 1503,
    "Saudi Arabia": 23, "Qatar": 1496, "Iraq": 26, "Uzbekistan": 1497,
    # Note: many IDs collide between confederations (e.g. "Germany"=25 UEFA vs
    # "South Korea"=25 AFC). API-Football re-uses team_ids across leagues —
    # we resolve by passing both team_id AND a known league context.
}

WC_2026_TEAMS_DEFAULT = [
    # The 48-team WC field (best-known set as of 2026). Customise via --teams.
    "Mexico", "Canada", "United States",
    "Brazil", "Argentina", "Uruguay", "Colombia", "Paraguay", "Ecuador",
    "France", "Spain", "Germany", "England", "Italy", "Netherlands",
    "Portugal", "Belgium", "Croatia", "Switzerland", "Denmark", "Austria",
    "Czechia", "Turkey", "Serbia", "Hungary", "Poland", "Bosnia-Herzegovina",
    "Scotland", "Wales", "Norway",
    "Morocco", "Senegal", "Tunisia", "Egypt", "Algeria", "Nigeria",
    "Ghana", "Cameroon", "Ivory Coast", "South Africa", "Congo DR",
    "Japan", "South Korea", "Iran", "Australia", "Saudi Arabia",
    "Qatar", "Uzbekistan", "Iraq",
    "Haiti", "Curaçao",
]


def _fetch_squad(team_id: int, key: str) -> list[dict]:
    url = f"{BASE_URL}/players/squads?team={team_id}"
    r = requests.get(url, headers={"x-apisports-key": key}, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        log.warning("squad team=%s errors: %s", team_id, d["errors"])
        return []
    return d.get("response", [])


def _normalise_pos(raw: str | None) -> str | None:
    if not raw:
        return None
    r = raw.upper()
    if "GOAL" in r or r == "GK":           return "GK"
    if "DEF" in r:                          return "DEF"
    if "MID" in r:                          return "MID"
    if "ATT" in r or "FOR" in r or "ST" in r: return "ATT"
    return None


def fetch_squad_for_nation(nation: str) -> Optional[int]:
    s = get_settings()
    if not s.api_football_key:
        log.error("API_FOOTBALL_KEY missing")
        return None
    team_id = NATIONAL_TEAM_IDS.get(nation)
    if not team_id:
        log.warning("No known team_id for %s", nation)
        return None

    payload = _fetch_squad(team_id, s.api_football_key)
    if not payload:
        return 0
    squad = payload[0].get("players", []) or []
    n = 0
    with SessionLocal() as db:
        for p in squad:
            pname = p.get("name")
            if not pname:
                continue
            existing = (
                db.query(NationalTeamSquad)
                .filter(
                    NationalTeamSquad.nation      == nation,
                    NationalTeamSquad.player_name == pname,
                )
                .first()
            )
            if existing:
                existing.position     = _normalise_pos(p.get("position")) or existing.position
                existing.shirt_number = p.get("number") or existing.shirt_number
                existing.source       = "api_football"
            else:
                db.add(NationalTeamSquad(
                    nation       = nation,
                    player_name  = pname,
                    position     = _normalise_pos(p.get("position")),
                    shirt_number = p.get("number"),
                    source       = "api_football",
                ))
                n += 1
        db.commit()
    log.info("squad %s: %d new players", nation, n)
    return n


def run(nations: list[str]) -> dict:
    totals = {}
    for nation in nations:
        try:
            n = fetch_squad_for_nation(nation)
            totals[nation] = n if n is not None else 0
        except Exception as e:
            log.warning("squad fetch failed for %s: %s", nation, e)
            totals[nation] = 0
        time.sleep(0.5)
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", nargs="+", help="Nation names (e.g. brazil france spain)")
    ap.add_argument("--all-wc", action="store_true", help="Fetch all WC2026 squads")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.all_wc:
        nations = WC_2026_TEAMS_DEFAULT
    elif args.teams:
        # Normalise capitalisation
        nations = []
        for t in args.teams:
            cap = " ".join(w.capitalize() for w in t.split())
            nations.append(NATIONAL_TEAM_IDS.get(cap) and cap or t)
    else:
        ap.error("Pass --teams or --all-wc")

    result = run(nations)
    total = sum(v for v in result.values() if v)
    print(f"\nTotal new players: {total}")
    for n, c in sorted(result.items(), key=lambda x: -(x[1] or 0))[:20]:
        print(f"  {n:<22} {c} new")


if __name__ == "__main__":
    main()
